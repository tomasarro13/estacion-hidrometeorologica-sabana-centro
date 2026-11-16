#!/usr/bin/env bash
# Publica la carpeta wiki/ de este repositorio en la Wiki de GitHub.
# Uso (desde la raíz del repositorio):  bash publicar_wiki.sh
set -euo pipefail
WIKI=https://github.com/tomasarro13/estacion-hidrometeorologica-sabana-centro.wiki.git
tmp=$(mktemp -d)
git clone -q "$WIKI" "$tmp"
cp wiki/*.md "$tmp"/
cd "$tmp"
git add -A
if git diff --cached --quiet; then
  echo "La Wiki ya está al día."
else
  git commit -q -m "Wiki Challenge 3: paginas C3.1 a C3.15, inicio y barra lateral"
  git push -q
  echo "Wiki publicada."
fi
