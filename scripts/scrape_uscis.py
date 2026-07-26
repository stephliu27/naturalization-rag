import requests
from bs4 import BeautifulSoup
from pprint import pprint

# Extract raw text from USCIS policy manual page
response = requests.get("https://www.uscis.gov/policy-manual/table-of-contents")
soup = BeautifulSoup(response.text, "html.parser")

# Loop through all level 2 divs to find volume 12 (Citizenship and Naturalization)
all_volumes = soup.find_all("div", class_="level--2")
volume_12 = None

for volume in all_volumes:
    title_tag = volume.find("div", class_="level__title")
    title_link = title_tag.find("a")
    if "Volume 12" in title_link.get_text(strip=True):
        volume_12 = volume
        break

""" # Safeguard to check that volume 12 was found and is correct
if volume_12 is None:
    raise ValueError("Volume 12 not found in the table of contents.")
print(volume_12.find("div", class_="level__title").get_text()) """


# Find all part blocks within volume 12
all_parts_raw = []
current = volume_12.find_next_sibling()

# Ensure we are only collecting parts within volume 12
while current is not None and "level--2" not in current.get("class", []):
    if "level--3" in current.get("class", []):
        all_parts_raw.append(current)
    current = current.find_next_sibling()
print(len(all_parts_raw))


# Extract part title, URL, and chapter information from each part block.
# Build dictionary for each part and append to list of parts.
all_parts_master = []

for part in all_parts_raw:
    part_title_tag = part.find("li", class_="level__item--3")
    part_link_tag = part_title_tag.find("a")
    part_url = "https://www.uscis.gov" + part_link_tag.get("href")
    part_title = part_link_tag.get_text(strip=True)

    # Extract chapter information for single part
    chapters = []
    chapter_wrapper = part.find("ul", class_="level--4")
    chapter_blocks = chapter_wrapper.find_all("li", class_="level__item--4")
    for chapter in chapter_blocks:
        chapter_link_tag = chapter.find("a")
        chapter_url = "https://www.uscis.gov" + chapter_link_tag.get("href")
        chapter_title = chapter_link_tag.get_text(strip=True)
        chapters.append({"chapter_title": chapter_title, "chapter_url": chapter_url})

    # Append part information to master list
    all_parts_master.append({
        "title": part_title,
        "url": part_url,
        "chapters": chapters
    })