#!/usr/bin/env bash
# Usuwa sterownik (pakiet cheerdots). 'purge' usuwa też ustawienia i parowanie.
if [ "${1:-}" = "purge" ]; then sudo apt-get purge -y cheerdots; else sudo apt-get remove -y cheerdots; fi
