## scrape table of contents to form list of part URLS to further scrape
base_part_url = "https://www.uscis.gov/policy-manual/volume-12-part-"
part_letters = "abcdefghijkl"
part_urls = []

for letter in part_letters:
    part_url = base_part_url + letter
    part_urls.append(part_url)

print(part_urls)