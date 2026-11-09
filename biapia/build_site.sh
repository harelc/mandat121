#!/usr/bin/env bash
# Assemble the bia-pia publish directory.
# Run by Netlify (base directory = biapia/, see netlify.toml here), or
# manually from this folder. The game page is biapia/index.html; the data
# it fetches (data/bills.json, mk_photos.json, mk_wiki.json, photos/) has a
# single source of truth in the repo-root /data, shared with המנדט ה־121.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf dist
mkdir -p dist/data
cp index.html dist/
cp ../data/bills.json ../data/mk_photos.json ../data/mk_wiki.json dist/data/
if [ -d ../data/photos ]; then cp -R ../data/photos dist/data/photos; fi
echo "biapia dist assembled: $(du -sh dist | cut -f1)"
