#!/usr/bin/env bash
# Project a captured interface; never generate or redraw its UI.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
command -v magick >/dev/null || { echo 'ImageMagick 7 is required.' >&2; exit 1; }
source_image="$root/docs/images/venus-demo.jpg"
[[ "$(magick identify -format '%wx%h' "$source_image")" == '1600x1000' ]] || { echo 'Screenshot dimensions changed; review the projection.' >&2; exit 1; }
render_dir="$(mktemp -d "${TMPDIR:-/tmp}/venus-os-observability-hero.XXXXXX")"
trap 'rm -rf -- "$render_dir"' EXIT
magick "$source_image" -alpha on \
  \( -size 1600x1000 xc:none -fill white -draw 'roundrectangle 0,0 1599,999 18,18' \) \
  -compose DstIn -composite -compose Over -virtual-pixel transparent -filter Robidoux \
  -define distort:viewport=1600x1050+0+0 -distort Perspective '0,0 145,172 1599,0 1385,97 1599,999 1450,872 0,999 210,947' -strip "$render_dir/panel.png"
magick "$render_dir/panel.png" -fill '#152033' -colorize 100 -channel A -evaluate multiply 0.23 +channel -blur 0x18 -strip "$render_dir/shadow.png"
magick -size 1600x1050 xc:'#eef2f7' "$render_dir/shadow.png" -geometry +0+22 -compose Over -composite \
  "$render_dir/panel.png" -geometry +0+0 -compose Over -composite -strip -define png:exclude-chunk=date,time "PNG24:$render_dir/hero.png"
mv -- "$render_dir/hero.png" "$root/docs/images/app-hero.png"
