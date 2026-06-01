#!/usr/bin/env bash
# Transcribe un video/audio a texto (es) usando whisper.cpp.
# Uso: tools/transcribe.sh "<ruta_video>" [idioma]
# Salida: transcripts/<nombre>.txt  y  transcripts/<nombre>.srt
set -euo pipefail

INPUT="${1:?Falta la ruta del video. Uso: tools/transcribe.sh <video> [idioma]}"
LANG="${2:-es}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
MODEL="$SCRIPT_DIR/whisper-models/ggml-large-v3-turbo.bin"
OUT_DIR="$ROOT_DIR/transcripts"
mkdir -p "$OUT_DIR"

base="$(basename "$INPUT")"
name="${base%.*}"
wav="$OUT_DIR/$name.wav"
out_base="$OUT_DIR/$name"

echo ">> Extrayendo audio (16kHz mono)..."
ffmpeg -y -i "$INPUT" -ar 16000 -ac 1 -c:a pcm_s16le "$wav" -loglevel error

echo ">> Transcribiendo con whisper.cpp (idioma=$LANG)..."
whisper-cli -m "$MODEL" -f "$wav" -l "$LANG" -otxt -osrt -of "$out_base" -pp

rm -f "$wav"
echo ">> Listo:"
echo "   $out_base.txt"
echo "   $out_base.srt"
