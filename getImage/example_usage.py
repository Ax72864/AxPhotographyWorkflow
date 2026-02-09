#!/usr/bin/env python3
"""
Quick start example for the Multi-Source Rated Images Downloader

This example demonstrates basic usage of the tool.
"""

import os
import sys

def main():
    print("=" * 70)
    print("Multi-Source Rated Images Downloader - Quick Start Example")
    print("=" * 70)
    print()
    
    # Check if config exists
    if not os.path.exists('config.ini'):
        print("⚠ Warning: config.ini not found in current directory")
        print("  Please make sure you're running this from the getImage/ directory")
        print()
    
    print("Available commands:")
    print()
    print("1. Setup KonIQ-10k dataset (recommended first step):")
    print("   python setup_koniq_dataset.py")
    print()
    print("2. Download 20 images with automatic distribution:")
    print("   python download_rated_images.py --count 20")
    print()
    print("3. Download 50 images with custom distribution:")
    print("   python download_rated_images.py --count 50 --high 15 --medium 20 --low 15")
    print()
    print("4. Use only KonIQ and Wiki Commons (no Flickr API needed):")
    print("   python download_rated_images.py --count 30 --sources koniq,wiki")
    print()
    print("5. Download with verbose logging:")
    print("   python download_rated_images.py --count 10 --verbose")
    print()
    
    print("-" * 70)
    print()
    print("Before running:")
    print("  1. Install dependencies: pip install -r requirements.txt")
    print("  2. (Optional) Configure Flickr API in config.ini")
    print("  3. (Recommended) Run setup_koniq_dataset.py to download expert-rated dataset")
    print()
    print("For full documentation, see README.md")
    print()

if __name__ == '__main__':
    main()
