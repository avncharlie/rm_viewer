#!/bin/bash
#cd ~
echo '{"requests":[{"image":{"content":"' > ./temp.json
openssl base64 -A -in "$1" >> ./temp.json
echo '"},
"features":{"type":"TEXT_DETECTION","maxResults":2048},"imageContext":{"languageHints":"en"}}]}' >> ./temp.json
curl -s -H "Content-Type: application/json" https://vision.googleapis.com/v1/images:annotate?key=$2 --data-binary @./temp.json > "$1.json"
rm ./temp.json
