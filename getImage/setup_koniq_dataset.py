#!/usr/bin/env python3
"""
KonIQ-10k Dataset Setup Script

This script downloads and sets up the KonIQ-10k image quality assessment dataset.
The dataset contains 10,073 images with expert quality ratings (MOS scores).

Official page: http://database.mmsp-kn.de/koniq-10k-database.html
"""

import os
import sys
import requests
import zipfile
from pathlib import Path
from tqdm import tqdm


class KonIQDatasetSetup:
    """Setup utility for KonIQ-10k dataset"""
    
    # Official download URLs (verified working as of 2026-01)
    DATASET_URLS = {
        'images': 'http://datasets.vqa.mmsp-kn.de/archives/koniq10k_1024x768.zip',
        'metadata': 'http://datasets.vqa.mmsp-kn.de/archives/koniq10k_scores_and_distributions.zip',
        'indicators': 'http://datasets.vqa.mmsp-kn.de/archives/koniq10k_indicators.zip'  # Optional
    }
    
    # Alternative: Google Drive links (require manual download)
    MANUAL_INSTRUCTIONS = """
    如果自动下载失败，请手动下载：
    
    方法1: 访问官方下载页面
    图片: http://datasets.vqa.mmsp-kn.de/archives/koniq10k_1024x768.zip
    评分: http://datasets.vqa.mmsp-kn.de/archives/koniq10k_scores_and_distributions.zip
    
    方法2: 从GitHub获取信息
    https://github.com/subpic/koniq-PyTorch
    
    下载后:
    1. 解压图片到: {images_dir}
    2. 解压评分CSV到: {metadata_file}
    """
    
    def __init__(self, base_dir='./datasets'):
        self.base_dir = Path(base_dir)
        self.koniq_dir = self.base_dir / 'koniq10k'
        self.images_dir = self.koniq_dir / 'images'
        self.metadata_file = self.koniq_dir / 'koniq10k_scores_and_distributions.csv'
        self.temp_dir = Path('./temp')
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
    def setup(self):
        """Main setup routine"""
        print("=" * 60)
        print("KonIQ-10k Dataset Setup")
        print("=" * 60)
        
        # Create directories
        self.koniq_dir.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if already downloaded
        if self.check_existing():
            print("\n✓ KonIQ-10k dataset already exists and is complete!")
            print(f"  Location: {self.koniq_dir.absolute()}")
            return True
        
        print("\nThis script will download the KonIQ-10k dataset:")
        print(f"  • ~2GB of images (10,073 photos)")
        print(f"  • Metadata CSV with quality scores")
        print(f"  • Download location: {self.koniq_dir.absolute()}")
        print("\nThe dataset is for academic research use.")
        
        response = input("\nContinue with download? (y/n): ")
        if response.lower() != 'y':
            print("Setup cancelled.")
            return False
        
        # Download metadata
        if not self.metadata_file.exists():
            print("\n[1/2] Downloading metadata CSV...")
            if not self.download_and_extract_metadata():
                print("\n  ✗ Failed to download metadata.")
                self._print_manual_instructions()
                return False
        else:
            print("\n[1/2] Metadata CSV already exists, skipping...")
        
        # Download images
        image_count = len(list(self.images_dir.glob('*.jpg')))
        if image_count < 10000:
            print(f"\n[2/2] Downloading images (found {image_count}/10073)...")
            if not self.download_and_extract_images():
                print("\n  ✗ Failed to download images.")
                self._print_manual_instructions()
                return False
        else:
            print(f"\n[2/2] Images already exist ({image_count} found), skipping...")
        
        # Verify
        if self.verify_dataset():
            print("\n" + "=" * 60)
            print("✓ Setup completed successfully!")
            print("=" * 60)
            print(f"\nDataset location: {self.koniq_dir.absolute()}")
            print(f"Images: {self.images_dir.absolute()}")
            print(f"Metadata: {self.metadata_file.absolute()}")
            return True
        else:
            print("\n✗ Setup incomplete. Please check the download and try again.")
            return False
    
    def check_existing(self):
        """Check if dataset already exists"""
        if not self.metadata_file.exists():
            return False
        
        image_count = len(list(self.images_dir.glob('*.jpg')))
        if image_count < 10000:
            return False
        
        return True
    
    def download_file(self, url, dest_path, chunk_size=8192):
        """Download a file with progress bar"""
        try:
            # Add headers to avoid blocking
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, stream=True, timeout=30, headers=headers, allow_redirects=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            with open(dest_path, 'wb') as f, tqdm(
                total=total_size,
                unit='B',
                unit_scale=True,
                desc=dest_path.name
            ) as pbar:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
            
            print(f"  ✓ Downloaded: {dest_path.name}")
            return True
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"  ✗ File not found (404): {url}")
            else:
                print(f"  ✗ HTTP error: {e}")
            return False
        except Exception as e:
            print(f"  ✗ Error: {e}")
            return False
    
    def download_and_extract_metadata(self):
        """Download and extract metadata CSV"""
        zip_path = self.temp_dir / 'koniq10k_scores.zip'
        
        # Download
        print(f"  Downloading metadata archive...")
        if not self.download_file(self.DATASET_URLS['metadata'], zip_path):
            return False
        
        # Extract
        print(f"  Extracting metadata CSV...")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Find the CSV file in the archive
                csv_files = [f for f in zip_ref.namelist() if f.endswith('.csv') and 'scores_and_distributions' in f]
                if not csv_files:
                    print(f"  ✗ CSV file not found in archive")
                    return False
                
                # Extract the CSV file
                csv_file = csv_files[0]
                source = zip_ref.open(csv_file)
                with source, open(self.metadata_file, 'wb') as target:
                    target.write(source.read())
            
            print(f"  ✓ Metadata extracted successfully")
            
            # Clean up zip file
            zip_path.unlink()
            
            return True
            
        except Exception as e:
            print(f"  ✗ Error extracting metadata: {e}")
            return False
    
    def download_and_extract_images(self):
        """Download and extract image archive"""
        zip_path = self.temp_dir / 'koniq10k_images.zip'
        
        # Download
        print(f"  Downloading image archive (~2GB)...")
        if not self.download_file(self.DATASET_URLS['images'], zip_path):
            return False
        
        # Extract
        print(f"  Extracting images...")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Extract to images directory
                members = zip_ref.namelist()
                for member in tqdm(members, desc="Extracting"):
                    # Extract only .jpg files to the images directory
                    if member.endswith('.jpg'):
                        filename = os.path.basename(member)
                        source = zip_ref.open(member)
                        target = open(self.images_dir / filename, 'wb')
                        with source, target:
                            target.write(source.read())
            
            print("  ✓ Extraction complete")
            
            # Clean up zip file
            zip_path.unlink()
            print("  ✓ Cleaned up temporary files")
            
            return True
            
        except Exception as e:
            print(f"  ✗ Error extracting archive: {e}")
            return False
    
    def verify_dataset(self):
        """Verify dataset integrity"""
        print("\nVerifying dataset...")
        
        # Check metadata
        if not self.metadata_file.exists():
            print("  ✗ Metadata file not found")
            return False
        print(f"  ✓ Metadata file exists")
        
        # Check images
        image_count = len(list(self.images_dir.glob('*.jpg')))
        print(f"  ✓ Found {image_count} images")
        
        if image_count < 10000:
            print(f"  ⚠ Warning: Expected ~10,073 images, found {image_count}")
            print(f"    This may be acceptable if the dataset was partially downloaded.")
        
        # Try to read metadata
        try:
            import pandas as pd
            df = pd.read_csv(self.metadata_file)
            print(f"  ✓ Metadata contains {len(df)} entries")
            
            # Check required columns
            required_cols = ['image_name', 'MOS']
            if all(col in df.columns for col in required_cols):
                print(f"  ✓ Metadata has required columns")
            else:
                print(f"  ✗ Metadata missing required columns")
                return False
                
        except Exception as e:
            print(f"  ✗ Error reading metadata: {e}")
            return False
        
        return True
    
    def _print_manual_instructions(self):
        """Print manual download instructions"""
        print("\n" + "=" * 70)
        print("手动下载指南 (Manual Download Instructions)")
        print("=" * 70)
        instructions = self.MANUAL_INSTRUCTIONS.format(
            images_dir=self.images_dir.absolute(),
            metadata_file=self.metadata_file.absolute()
        )
        print(instructions)
        print("=" * 70)


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Download and setup KonIQ-10k dataset'
    )
    parser.add_argument(
        '--base-dir',
        default='./datasets',
        help='Base directory for datasets (default: ./datasets)'
    )
    
    args = parser.parse_args()
    
    setup = KonIQDatasetSetup(base_dir=args.base_dir)
    success = setup.setup()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
