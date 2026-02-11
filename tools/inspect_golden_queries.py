from chromadb import PersistentClient

client = PersistentClient(path="/home/parthibanprabhakara/hf_aio/data/vectorstore")
golden = client.get_collection("golden_queries")

results = golden.get(include=["documents", "metadatas"])
print(results)
