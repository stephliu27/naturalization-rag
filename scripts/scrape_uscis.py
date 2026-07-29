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
for part in all_parts_master:
    for chapter in part["chapters"]:
        chapter_response = requests.get(chapter["chapter_url"])
        chapter_soup = BeautifulSoup(chapter_response.text, "html.parser")
        guidance = chapter_soup.find("div", id="guidance")  # Find guidance container under which content lies
        chapter_body = guidance.find("div", class_="field--name-body")
        text_parts = []

        # Applying formatting to the extracted text based on HTML structure (paragraph, list, table, etc.)
        for child in chapter_body.find_all(recursive=False):
            if child.name in ["p", "h2", "h3", "h4"]:
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