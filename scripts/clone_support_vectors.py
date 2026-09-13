"""Copy active local Qdrant collections using consistent snapshots, never live storage files."""
import tempfile
from pathlib import Path
import httpx

source = "http://valerchik_qdrant_1:6333"
target = "http://supportflow-qdrant:6333"
with httpx.Client(timeout=300) as client:
    aliases = client.get(source + "/aliases").raise_for_status().json()["result"]["aliases"]
    for collection in sorted({a["collection_name"] for a in aliases}):
        before = client.get(f"{source}/collections/{collection}").raise_for_status().json()["result"]
        name = client.post(f"{source}/collections/{collection}/snapshots").raise_for_status().json()["result"]["name"]
        with tempfile.TemporaryDirectory(prefix="support-snapshot-") as directory:
            snapshot = Path(directory) / name
            with client.stream("GET", f"{source}/collections/{collection}/snapshots/{name}") as response:
                response.raise_for_status()
                with snapshot.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            with snapshot.open("rb") as stream:
                client.post(f"{target}/collections/{collection}/snapshots/upload?priority=snapshot",
                            files={"snapshot": (name, stream, "application/octet-stream")}).raise_for_status()
        after = client.get(f"{target}/collections/{collection}").raise_for_status().json()["result"]
        assert after["points_count"] == before["points_count"], (before["points_count"], after["points_count"])
        print(collection, "points:", after["points_count"], flush=True)
        client.delete(f"{source}/collections/{collection}/snapshots/{name}").raise_for_status()
    client.post(target + "/collections/aliases", json={"actions": [
        {"create_alias": a} for a in aliases]}).raise_for_status()
    print("Aliases copied", flush=True)
