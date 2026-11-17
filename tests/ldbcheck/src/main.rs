// Opens a LevelDB database with rusty-leveldb (the library Arnis uses for Bedrock worlds), with the
// game's two deflate block formats registered and paranoid checks on, reads every record, and prints the
// count and an FNV-1a hash of all keys and values in key order.
use rusty_leveldb::compressor::NoneCompressor;
use rusty_leveldb::{Compressor, CompressorList, LdbIterator, Options, DB};
use std::io::Read;
use std::rc::Rc;

struct Deflate(bool);
impl Compressor for Deflate {
    fn encode(&self, _block: Vec<u8>) -> rusty_leveldb::Result<Vec<u8>> {
        unimplemented!("read only")
    }
    fn decode(&self, block: Vec<u8>) -> rusty_leveldb::Result<Vec<u8>> {
        let mut out = Vec::new();
        let r = if self.0 {
            flate2::read::ZlibDecoder::new(&block[..]).read_to_end(&mut out)
        } else {
            flate2::read::DeflateDecoder::new(&block[..]).read_to_end(&mut out)
        };
        r.map_err(|e| rusty_leveldb::Status::new(rusty_leveldb::StatusCode::CompressionError, &e.to_string()))?;
        Ok(out)
    }
}

fn main() {
    let path = std::env::args().nth(1).expect("usage: ldbcheck DB_DIR");
    let mut list = CompressorList::new();
    list.set_with_id(0, NoneCompressor);
    list.set_with_id(2, Deflate(true));
    list.set_with_id(4, Deflate(false));
    let mut opt = Options::default();
    opt.compressor_list = Rc::new(list);
    opt.create_if_missing = false;
    opt.paranoid_checks = true;
    let mut db = DB::open(&path, opt).expect("open failed");
    let mut it = db.new_iter().expect("iterator failed");
    let (mut n, mut total, mut hash) = (0u64, 0u64, 1469598103934665603u64);
    let mut last: Vec<u8> = Vec::new();
    while let Some((k, v)) = it.next() {
        assert!(n == 0 || k.as_slice() > last.as_slice(), "keys out of order");
        for b in k.iter().chain(v.iter()) {
            hash = (hash ^ (*b as u64)).wrapping_mul(1099511628211);
        }
        total += (k.len() + v.len()) as u64;
        last = k.to_vec();
        n += 1;
    }
    let probe = db.get(&[0u8, 0, 0, 0, 0, 0, 0, 0, 44]);
    println!("records {} bytes {} hash {:016x} chunk(0,0) version record {:?}", n, total, hash, probe.map(|v| v.to_vec()));
}
