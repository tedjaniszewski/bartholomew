import requests

url = (
    "https://www.geographicus.com/mm5/graphics/00000001/"
    "zoomify/TopoPhysicalPalestine-bartholomew-1901/"
    "ImageProperties.xml"
)

r = requests.get(
    url,
    headers={
        "User-Agent": "Mozilla/5.0"
    },
    timeout=30
)

print("HTTP status:", r.status_code)
print()
print(r.text)