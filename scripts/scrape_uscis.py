import requests
import re
import os
import json
from bs4 import BeautifulSoup
from pprint import pprint
from datetime import date

# Function to create a safe streamlined filename from a given text
def make_safe_filename(text):
    text = text.lower().replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


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

# Find all part blocks within volume 12
all_parts_raw = []
current = volume_12.find_next_sibling()

# Ensure we are only collecting parts within volume 12
while current is not None and "level--2" not in current.get("class", []):
    if "level--3" in current.get("class", []):
        all_parts_raw.append(current)
    current = current.find_next_sibling()


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
        "chapters": chapters  # Each chapter has its own chapter_title and chapter_url
    })


# Fetch the content of each chapter and create files for each chapter's body text and metadata
os.makedirs("data/raw", exist_ok=True)  # Create new directory to store scraped text files
failed_chapters = []  # List to keep track of chapters that failed to scrape

for part in all_parts_master:
    part_name = make_safe_filename(part['title'])

    for chapter in part["chapters"]:
        # Attempt to fetch chapter page, skip this chapter and record failure if request fails
        try:
            chapter_response = requests.get(chapter["chapter_url"])
        except Exception as e:
            failed_chapters.append({
                "part_title": part['title'],
                "chapter_title": chapter['chapter_title'],
                "url": chapter['chapter_url'],
                "error": str(e)
            })
            continue

        # Successfully obtained chapter content, now extract the body text
        chapter_soup = BeautifulSoup(chapter_response.text, "html.parser")
        guidance = chapter_soup.find("div", id="guidance")  # Find guidance container under which content lies

        # If guidance container is not found, probably 404 error, so record failure and skip this chapter
        if guidance is None:
            failed_chapters.append({
                "part_title": part['title'],
                "chapter_title": chapter['chapter_title'],
                "url": chapter['chapter_url'],
                "error": "Could not find 'guidance' div — page may be missing or structured differently"
            })
            continue

        # Perform same check for finding chapter body content within guidance container, if not found, record failure and skip this chapter
        chapter_body = guidance.find("div", class_="field--name-body")
        if chapter_body is None:
            failed_chapters.append({
                "part_title": part['title'],
                "chapter_title": chapter['chapter_title'],
                "url": chapter['chapter_url'],
                "error": "Could not find 'field--name-body' div inside guidance section"
            })
            continue

        text_parts = []

        # Applying formatting to the extracted text based on HTML structure (paragraph, list, table, etc.)
        for child in chapter_body.find_all(recursive=False):
            if child.name in ["p", "h2", "h3", "h4", "section"]:
                text = child.get_text(strip=True)
                if text:
                    text_parts.append(text)

            elif child.name in ["ul", "ol"]:
                items = child.find_all("li")
                for item in items:
                    item_text = item.get_text(strip=True)
                    if item_text:
                        text_parts.append(f"- {item_text}")  # Reformat to reflect bullet list style

            elif child.name == "table":
                rows = child.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    row_line = "| " + " | ".join(cell_texts) + " |"   # Reformat for markdown table style
                    text_parts.append(row_line)

        chapter_body = "\n".join(text_parts)

        # Create file names for chapter body text and metadata
        chapter_name = make_safe_filename(chapter['chapter_title'])
        filename = f"data/raw/{part_name}_{chapter_name}.txt"
        metadata_filename = f"data/raw/{part_name}_{chapter_name}_metadata.json"

        # Create a file for each chapter's body text
        with open(filename, "w") as f:
            f.write(chapter_body)

        # Create a metadata sidecar for each chapter
        metadata = {
            "part_title": part['title'],
            "chapter_title": chapter['chapter_title'],
            "chapter_url": chapter['chapter_url'],
            "scraped_date": str(date.today())
        }

        with open(metadata_filename, "w") as f:
            json.dump(metadata, f, indent=2)

print(f"\nScrape complete. {len(failed_chapters)} chapter(s) failed:")
for failure in failed_chapters:
    print(f"  - {failure['part_title']} / {failure['chapter_title']} ({failure['url']}): {failure['error']}")