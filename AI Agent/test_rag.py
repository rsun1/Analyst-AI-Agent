import sys
sys.stdout.reconfigure(encoding="utf-8")
from rag import retrieve

test_queries = [
    "electronics category average price",
    "sports leisure delivery time",
    "books general interest reviews"
]

for q in test_queries:
    print(f"\nQuery: {q}")
    chunks = retrieve(q)
    for i, chunk in enumerate(chunks):
        print(f"  Chunk {i+1}: {chunk[:200]}")
