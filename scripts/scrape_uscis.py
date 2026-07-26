import requests
from bs4 import BeautifulSoup

# Extract raw text from USCIS policy manual page
response = requests.get("https://www.uscis.gov/policy-manual/table-of-contents")
soup = BeautifulSoup(response.text, "html.parser")

# Loop through all level 2 divs to find volume 12
all_volumes = soup.find_all("div", class_="level--2")
volume_12 = None

for volume in all_volumes:
    title_tag = volume.find("div", class_="level__title")
    title_link = title_tag.find("a")
    if "Volume 12" in title_link.text:
        volume_12 = volume
        break

# Safeguard to check that volume 12 was found and is correct
if volume_12 is None:
    raise ValueError("Volume 12 not found in the table of contents.")
print(volume_12.find("div", class_="level__title").get_text())

""" scrape table of contents to form list of part URLS to further scrape
base_part_url = "https://www.uscis.gov/policy-manual/volume-12-part-"
part_letters = "abcdefghijkl"
part_urls = []

for letter in part_letters:
    part_url = base_part_url + letter
    part_urls.append(part_url)

print(part_urls) """