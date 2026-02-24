from backend.app.core.config import settings
from redis import Redis
import os
import pickle

def check_redis():
    r = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD,
        decode_responses=False # Arq uses pickle, need bytes
    )
    print(f"Connecting to Redis at {settings.REDIS_HOST}:{settings.REDIS_PORT}")
    try:
        keys = r.keys("*")
        print(f"Total keys: {len(keys)}")
        for key in keys:
            key_str = key.decode()
            t = r.type(key).decode()
            print(f"Key: {key_str}, Type: {t}")
            if t == "zset":
                members = r.zrange(key, 0, -1, withscores=True)
                for member, score in members:
                    print(f"  Member: {member.decode() if isinstance(member, bytes) else member}, Score: {score}")
            elif t == "hash":
                print(f"  Hash keys: {r.hkeys(key)}")
            elif t == "string":
                val = r.get(key)
                try:
                    # Arq results are usually pickled
                    data = pickle.loads(val)
                    print(f"  Pickled data: {data}")
                except:
                    print(f"  Raw value: {val[:50]!r}")
    except Exception as e:
        print(f"Error connecting to Redis: {e}")

if __name__ == "__main__":
    check_redis()
