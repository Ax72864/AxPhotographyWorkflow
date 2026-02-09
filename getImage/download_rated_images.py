#!/usr/bin/env python3
"""
Multi-Source Rated Images Downloader

This tool downloads rated images from multiple sources (Flickr, KonIQ-10k, Wiki Commons)
with consistent quality scoring (0-10) for evaluating LLM photo rating systems.
"""

import os
import sys
import json
import hashlib
import re
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import configparser

import requests
from PIL import Image
import numpy as np


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class RatedImage:
    """Container for image metadata and score"""
    url: str
    title: str
    author: str
    score: float  # 0-10 normalized score
    source: str  # 'flickr', 'koniq', 'wiki'
    metadata: Dict  # Additional source-specific metadata
    

class BaseImageSource(ABC):
    """Abstract base class for image sources"""
    
    def __init__(self, config: configparser.ConfigParser):
        self.config = config
        self.name = self.__class__.__name__
    
    @abstractmethod
    def fetch_images(self, count: int, score_range: Optional[Tuple[float, float]] = None) -> List[RatedImage]:
        """
        Fetch images from the source
        
        Args:
            count: Number of images to fetch
            score_range: Optional (min, max) score range filter
            
        Returns:
            List of RatedImage objects
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the source is available and configured"""
        pass
    
    def normalize_score(self, raw_score: float, min_val: float, max_val: float) -> float:
        """
        Normalize a score to 0-10 range
        
        Args:
            raw_score: Raw score value
            min_val: Minimum possible value
            max_val: Maximum possible value
            
        Returns:
            Normalized score (0-10)
        """
        if max_val == min_val:
            return 5.0
        
        normalized = ((raw_score - min_val) / (max_val - min_val)) * 10.0
        return max(0.0, min(10.0, normalized))
    
    def sanitize_filename(self, text: str, max_length: int = 50) -> str:
        """
        Sanitize text for use in filenames
        
        Args:
            text: Text to sanitize
            max_length: Maximum length
            
        Returns:
            Sanitized string safe for filenames
        """
        # Remove or replace invalid characters
        text = re.sub(r'[<>:"/\\|?*]', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        # Truncate if too long
        if len(text) > max_length:
            text = text[:max_length].rsplit(' ', 1)[0]
        
        return text if text else 'untitled'


class DeduplicationManager:
    """Manages downloaded image tracking to prevent duplicates"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db = self._load_db()
    
    def _load_db(self) -> Dict:
        """Load deduplication database"""
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load deduplication DB: {e}")
                return {'downloaded': {}, 'hashes': {}}
        return {'downloaded': {}, 'hashes': {}}
    
    def _save_db(self):
        """Save deduplication database"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.db, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save deduplication DB: {e}")
    
    def is_downloaded(self, url: str) -> bool:
        """Check if URL has been downloaded"""
        return url in self.db['downloaded']
    
    def is_duplicate_hash(self, image_hash: str) -> bool:
        """Check if image hash already exists"""
        return image_hash in self.db['hashes']
    
    def add_entry(self, url: str, filename: str, image_hash: str):
        """Add downloaded image entry"""
        self.db['downloaded'][url] = {
            'filename': filename,
            'hash': image_hash
        }
        self.db['hashes'][image_hash] = filename
        self._save_db()
    
    def compute_image_hash(self, image_path: Path) -> str:
        """Compute perceptual hash of image"""
        try:
            with Image.open(image_path) as img:
                # Resize to small size for comparison
                img = img.resize((8, 8), Image.Resampling.LANCZOS).convert('L')
                pixels = list(img.getdata())
                avg = sum(pixels) / len(pixels)
                
                # Create hash based on average
                bits = ''.join('1' if p > avg else '0' for p in pixels)
                return hashlib.md5(bits.encode()).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to compute image hash: {e}")
            return hashlib.md5(str(image_path).encode()).hexdigest()


class ImageDownloader:
    """Main controller for downloading rated images from multiple sources"""
    
    def __init__(self, config_path: str = 'config.ini'):
        self.config = self._load_config(config_path)
        self.output_dir = Path(self.config.get('download', 'output_dir', fallback='./rated'))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize deduplication
        self.dedup = DeduplicationManager(self.output_dir.parent / 'downloaded.json')
        
        # Initialize sources (will be populated later)
        self.sources: List[BaseImageSource] = []
        self.current_source_index = 0
    
    def _load_config(self, config_path: str) -> configparser.ConfigParser:
        """Load configuration file"""
        config = configparser.ConfigParser()
        
        if os.path.exists(config_path):
            config.read(config_path, encoding='utf-8')
        else:
            logger.warning(f"Config file not found: {config_path}")
            # Create default sections
            config['download'] = {'output_dir': './rated'}
        
        return config
    
    def register_source(self, source: BaseImageSource):
        """Register an image source"""
        if source.is_available():
            self.sources.append(source)
            logger.info(f"Registered source: {source.name}")
        else:
            logger.warning(f"Source not available: {source.name}")
    
    def download_images(
        self,
        count: int,
        score_distribution: Optional[Dict[str, int]] = None
    ) -> List[Path]:
        """
        Download images from available sources
        
        Args:
            count: Total number of images to download
            score_distribution: Optional dict with 'high', 'medium', 'low' counts
            
        Returns:
            List of downloaded image paths
        """
        if not self.sources:
            logger.error("No image sources available")
            return []
        
        downloaded = []
        
        # Determine score distribution
        if score_distribution:
            targets = score_distribution
        else:
            # Auto-distribute: 30% low, 40% medium, 30% high
            targets = {
                'low': int(count * 0.3),
                'medium': int(count * 0.4),
                'high': count - int(count * 0.3) - int(count * 0.4)
            }
        
        logger.info(f"Target distribution: {targets}")
        
        # Fetch images by score range
        score_ranges = {
            'high': (7.0, 10.0),
            'medium': (4.0, 7.0),
            'low': (0.0, 4.0)
        }
        
        for category, target_count in targets.items():
            if target_count <= 0:
                continue
            
            score_range = score_ranges[category]
            logger.info(f"Fetching {target_count} {category} quality images (score {score_range[0]}-{score_range[1]})")
            
            category_downloaded = self._fetch_and_download(target_count, score_range)
            downloaded.extend(category_downloaded)
        
        logger.info(f"Successfully downloaded {len(downloaded)}/{count} images")
        return downloaded
    
    def _fetch_and_download(
        self,
        count: int,
        score_range: Tuple[float, float]
    ) -> List[Path]:
        """Fetch and download images with score range from available sources"""
        downloaded = []
        attempts = 0
        max_attempts = count * 3  # Try up to 3x the target count
        
        while len(downloaded) < count and attempts < max_attempts:
            # Round-robin through sources
            source = self.sources[self.current_source_index]
            self.current_source_index = (self.current_source_index + 1) % len(self.sources)
            
            try:
                # Fetch batch from source
                batch_size = min(5, count - len(downloaded))
                images = source.fetch_images(batch_size, score_range)
                
                for img in images:
                    if len(downloaded) >= count:
                        break
                    
                    # Skip if already downloaded
                    if self.dedup.is_downloaded(img.url):
                        logger.debug(f"Skipping duplicate URL: {img.url}")
                        continue
                    
                    # Download image
                    path = self._download_image(img)
                    if path:
                        downloaded.append(path)
                
                attempts += batch_size
                
            except Exception as e:
                logger.error(f"Error fetching from {source.name}: {e}")
                attempts += 1
        
        return downloaded
    
    def _download_image(self, img: RatedImage) -> Optional[Path]:
        """Download a single image"""
        try:
            # Create filename: title-author-score.jpg
            title = self.sources[0].sanitize_filename(img.title, 30)
            author = self.sources[0].sanitize_filename(img.author, 20)
            score_str = f"{img.score:.1f}"
            
            filename = f"{title}-{author}-{score_str}.jpg"
            filepath = self.output_dir / filename
            
            # Handle local files (from KonIQ)
            if img.url.startswith('file://'):
                local_path = Path(img.metadata.get('local_path', img.url.replace('file://', '')))
                if not copy_local_file(local_path, filepath):
                    return None
            else:
                # Download from URL
                response = requests.get(img.url, timeout=30, stream=True)
                response.raise_for_status()
                
                # Save image
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            
            # Verify it's a valid image
            try:
                with Image.open(filepath) as test_img:
                    test_img.verify()
            except Exception as e:
                logger.warning(f"Invalid image file: {e}")
                if filepath.exists():
                    filepath.unlink()
                return None
            
            # Compute hash and check for duplicates
            img_hash = self.dedup.compute_image_hash(filepath)
            if self.dedup.is_duplicate_hash(img_hash):
                logger.debug(f"Skipping duplicate image (hash match): {filename}")
                filepath.unlink()
                return None
            
            # Register in deduplication DB
            self.dedup.add_entry(img.url, filename, img_hash)
            
            logger.info(f"Downloaded: {filename} (score: {img.score:.1f}, source: {img.source})")
            return filepath
            
        except Exception as e:
            logger.error(f"Failed to download {img.url}: {e}")
            return None


class FlickrSource(BaseImageSource):
    """Flickr API image source with multi-metric scoring"""
    
    def __init__(self, config: configparser.ConfigParser):
        super().__init__(config)
        self.api_key = config.get('flickr', 'api_key', fallback=None)
        self.api_secret = config.get('flickr', 'api_secret', fallback=None)
        self.base_url = 'https://api.flickr.com/services/rest/'
        
        # Cache for score distribution (for percentile calculation)
        self.score_cache = []
        self.cache_size = 1000
    
    def is_available(self) -> bool:
        """Check if Flickr API is configured"""
        return bool(self.api_key and self.api_key != 'YOUR_FLICKR_API_KEY')
    
    def fetch_images(self, count: int, score_range: Optional[Tuple[float, float]] = None) -> List[RatedImage]:
        """Fetch images from Flickr with scoring"""
        images = []
        
        try:
            # Search for interesting photos
            params = {
                'method': 'flickr.photos.search',
                'api_key': self.api_key,
                'format': 'json',
                'nojsoncallback': 1,
                'per_page': min(count * 3, 100),  # Fetch extra to filter by score
                'page': 1,
                'extras': 'url_l,url_c,views,count_faves,count_comments,owner_name',
                'sort': 'interestingness-desc' if score_range and score_range[0] >= 7 else 'relevance',
                'content_type': 1,  # Photos only
                'media': 'photos',
                'safe_search': 1  # Safe content
            }
            
            # Add text query for variety
            search_terms = ['landscape', 'portrait', 'nature', 'architecture', 'street', 'wildlife']
            import random
            params['text'] = random.choice(search_terms)
            
            response = requests.get(self.base_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get('stat') != 'ok':
                logger.error(f"Flickr API error: {data.get('message', 'Unknown error')}")
                return images
            
            photos = data.get('photos', {}).get('photo', [])
            
            for photo in photos:
                if len(images) >= count:
                    break
                
                # Get detailed info including stats
                img = self._process_photo(photo)
                if not img:
                    continue
                
                # Filter by score range if specified
                if score_range:
                    if not (score_range[0] <= img.score <= score_range[1]):
                        continue
                
                images.append(img)
            
            logger.info(f"Fetched {len(images)} images from Flickr")
            
        except Exception as e:
            logger.error(f"Flickr fetch error: {e}")
        
        return images
    
    def _process_photo(self, photo: Dict) -> Optional[RatedImage]:
        """Process a Flickr photo and calculate score"""
        try:
            # Get image URL (prefer large, fallback to medium)
            url = photo.get('url_l') or photo.get('url_c')
            if not url:
                return None
            
            # Extract metrics
            views = int(photo.get('views', 0))
            favorites = int(photo.get('count_faves', 0))
            comments = int(photo.get('count_comments', 0))
            
            # Calculate composite score
            score = self._calculate_flickr_score(views, favorites, comments)
            
            # Get title and author
            title = photo.get('title', 'Untitled')
            author = photo.get('ownername', 'Unknown')
            
            return RatedImage(
                url=url,
                title=title,
                author=author,
                score=score,
                source='flickr',
                metadata={
                    'photo_id': photo.get('id'),
                    'views': views,
                    'favorites': favorites,
                    'comments': comments
                }
            )
            
        except Exception as e:
            logger.debug(f"Error processing Flickr photo: {e}")
            return None
    
    def _calculate_flickr_score(self, views: int, favorites: int, comments: int) -> float:
        """
        Calculate normalized score from Flickr metrics
        
        Uses logarithmic transformation to handle large value ranges:
        score = 0.3*log10(views+1) + 0.5*log10(favorites*10+1) + 0.2*log10(comments*20+1)
        """
        # Logarithmic transformation
        v = np.log10(views + 1)
        f = np.log10(favorites * 10 + 1)
        c = np.log10(comments * 20 + 1)
        
        # Weighted combination
        raw_score = 0.3 * v + 0.5 * f + 0.2 * c
        
        # Add to cache for percentile calculation
        self.score_cache.append(raw_score)
        if len(self.score_cache) > self.cache_size:
            self.score_cache = self.score_cache[-self.cache_size:]
        
        # Normalize using percentile if we have enough data
        if len(self.score_cache) >= 20:
            percentile = np.percentile(self.score_cache, 
                                      [self.score_cache.index(s) / len(self.score_cache) * 100 
                                       for s in [raw_score]])[0]
            score = (percentile / 100) * 10
        else:
            # Use rough normalization (typical range 0-10)
            score = min(10.0, max(0.0, raw_score * 2))
        
        return round(score, 2)


class KonIQSource(BaseImageSource):
    """KonIQ-10k dataset source with expert MOS scores"""
    
    def __init__(self, config: configparser.ConfigParser):
        super().__init__(config)
        self.dataset_path = Path(config.get('koniq', 'dataset_path', fallback='./datasets/koniq10k'))
        self.images_dir = self.dataset_path / 'images'
        self.metadata_file = self.dataset_path / config.get('koniq', 'metadata_file', 
                                                            fallback='koniq10k_scores_and_distributions.csv')
        self.metadata = None
        self._load_metadata()
    
    def is_available(self) -> bool:
        """Check if KonIQ dataset is available"""
        return (self.images_dir.exists() and 
                self.metadata_file.exists() and 
                self.metadata is not None)
    
    def _load_metadata(self):
        """Load KonIQ metadata CSV"""
        try:
            import pandas as pd
            if self.metadata_file.exists():
                self.metadata = pd.read_csv(self.metadata_file)
                logger.info(f"Loaded KonIQ metadata: {len(self.metadata)} images")
            else:
                logger.warning(f"KonIQ metadata not found: {self.metadata_file}")
        except Exception as e:
            logger.error(f"Failed to load KonIQ metadata: {e}")
            self.metadata = None
    
    def fetch_images(self, count: int, score_range: Optional[Tuple[float, float]] = None) -> List[RatedImage]:
        """Fetch images from KonIQ dataset"""
        images = []
        
        if self.metadata is None:
            return images
        
        try:
            import pandas as pd
            
            # Filter by score range if specified
            df = self.metadata.copy()
            
            if score_range:
                # KonIQ MOS scores are typically 1-5, normalize to 0-10
                min_mos = score_range[0] / 2  # Convert from 0-10 to 1-5 range
                max_mos = score_range[1] / 2
                df = df[(df['MOS'] >= min_mos) & (df['MOS'] <= max_mos)]
            
            # Sample random images
            if len(df) > count:
                df = df.sample(n=count)
            
            for _, row in df.iterrows():
                img_name = row['image_name']
                img_path = self.images_dir / img_name
                
                if not img_path.exists():
                    continue
                
                # Normalize MOS score (typically 1-5) to 0-10
                mos = float(row['MOS'])
                normalized_score = self.normalize_score(mos, 1.0, 5.0)
                
                images.append(RatedImage(
                    url=f"file://{img_path.absolute()}",
                    title=img_name.replace('.jpg', '').replace('_', ' '),
                    author='KonIQ Dataset',
                    score=normalized_score,
                    source='koniq',
                    metadata={
                        'mos': mos,
                        'local_path': str(img_path)
                    }
                ))
            
            logger.info(f"Fetched {len(images)} images from KonIQ dataset")
            
        except Exception as e:
            logger.error(f"KonIQ fetch error: {e}")
        
        return images


class WikiCommonsSource(BaseImageSource):
    """Wiki Loves Monuments source with award-winning photos"""
    
    def __init__(self, config: configparser.ConfigParser):
        super().__init__(config)
        self.api_url = 'https://commons.wikimedia.org/w/api.php'
        
        # Award level to score mapping
        self.award_scores = {
            '1st': 10.0,
            '2nd': 9.5,
            '3rd': 9.0,
            'finalist': 8.5,
            'winner': 10.0,
            'featured': 8.0
        }
    
    def is_available(self) -> bool:
        """Wiki Commons is always available (no auth required)"""
        return True
    
    def fetch_images(self, count: int, score_range: Optional[Tuple[float, float]] = None) -> List[RatedImage]:
        """Fetch award-winning images from Wiki Commons"""
        images = []
        
        # Wiki sources are typically high-quality, skip if requesting low scores
        if score_range and score_range[1] < 7.0:
            logger.debug("Skipping Wiki Commons for low score range")
            return images
        
        try:
            # Search for Wiki Loves Monuments and other competition images
            categories = [
                'Images_from_Wiki_Loves_Monuments',
                'Featured_pictures_on_Wikimedia_Commons',
                'Quality_images_of_landscapes',
                'Quality_images_of_architecture'
            ]
            
            for category in categories:
                if len(images) >= count:
                    break
                
                batch = self._fetch_from_category(category, count - len(images))
                images.extend(batch)
            
            # Filter by score range
            if score_range:
                images = [img for img in images 
                         if score_range[0] <= img.score <= score_range[1]]
            
            logger.info(f"Fetched {len(images)} images from Wiki Commons")
            
        except Exception as e:
            logger.error(f"Wiki Commons fetch error: {e}")
        
        return images[:count]
    
    def _fetch_from_category(self, category: str, count: int) -> List[RatedImage]:
        """Fetch images from a specific Wiki Commons category"""
        images = []
        
        try:
            params = {
                'action': 'query',
                'format': 'json',
                'generator': 'categorymembers',
                'gcmtitle': f'Category:{category}',
                'gcmtype': 'file',
                'gcmlimit': min(count * 2, 50),
                'prop': 'imageinfo',
                'iiprop': 'url|user|size',
                'iiurlwidth': 1024
            }
            
            response = requests.get(self.api_url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            pages = data.get('query', {}).get('pages', {})
            
            for page_id, page in pages.items():
                if len(images) >= count:
                    break
                
                imageinfo = page.get('imageinfo', [])
                if not imageinfo:
                    continue
                
                info = imageinfo[0]
                url = info.get('url')
                thumb_url = info.get('thumburl', url)
                
                if not url:
                    continue
                
                # Determine score based on category and awards
                title = page.get('title', '').lower()
                score = self._determine_award_score(title, category)
                
                # Extract clean title
                clean_title = page.get('title', '').replace('File:', '').replace('.jpg', '').replace('_', ' ')
                
                images.append(RatedImage(
                    url=thumb_url or url,
                    title=clean_title[:50],
                    author=info.get('user', 'Wiki Commons'),
                    score=score,
                    source='wiki',
                    metadata={
                        'page_id': page_id,
                        'category': category,
                        'full_url': url
                    }
                ))
        
        except Exception as e:
            logger.debug(f"Error fetching from category {category}: {e}")
        
        return images
    
    def _determine_award_score(self, title: str, category: str) -> float:
        """Determine score based on title and category"""
        # Check for award indicators in title
        for award, score in self.award_scores.items():
            if award in title:
                return score
        
        # Default scores by category type
        if 'featured' in category.lower():
            return 9.0
        elif 'quality' in category.lower():
            return 8.5
        elif 'monuments' in category.lower():
            return 8.0
        
        return 8.0  # Default for wiki images


# Download helper function for local files (KonIQ)
def copy_local_file(source_path: Path, dest_path: Path) -> bool:
    """Copy a local file to destination"""
    try:
        import shutil
        shutil.copy2(source_path, dest_path)
        return True
    except Exception as e:
        logger.error(f"Failed to copy file: {e}")
        return False


def main():
    """Main CLI entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Download rated images from multiple sources for LLM evaluation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download 20 images with automatic distribution
  python download_rated_images.py --count 20
  
  # Download 50 images with custom distribution
  python download_rated_images.py --count 50 --high 15 --medium 20 --low 15
  
  # Download from specific sources only
  python download_rated_images.py --count 30 --sources flickr,koniq
  
  # Use custom config file
  python download_rated_images.py --count 10 --config my_config.ini
        """
    )
    
    parser.add_argument(
        '--count',
        type=int,
        default=20,
        help='Total number of images to download (default: 20)'
    )
    
    parser.add_argument(
        '--high',
        type=int,
        help='Number of high-quality images (7-10 score). If not specified, auto-distributes.'
    )
    
    parser.add_argument(
        '--medium',
        type=int,
        help='Number of medium-quality images (4-7 score). If not specified, auto-distributes.'
    )
    
    parser.add_argument(
        '--low',
        type=int,
        help='Number of low-quality images (0-4 score). If not specified, auto-distributes.'
    )
    
    parser.add_argument(
        '--sources',
        type=str,
        help='Comma-separated list of sources to use: flickr,koniq,wiki (default: all available)'
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config.ini',
        help='Path to config file (default: config.ini)'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        help='Output directory (overrides config file)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Set log level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Print header
    print("=" * 70)
    print("Multi-Source Rated Images Downloader")
    print("=" * 70)
    print()
    
    # Initialize downloader
    try:
        downloader = ImageDownloader(args.config)
    except Exception as e:
        logger.error(f"Failed to initialize downloader: {e}")
        return 1
    
    # Override output directory if specified
    if args.output:
        downloader.output_dir = Path(args.output)
        downloader.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialize sources
    config = downloader.config
    
    # Filter sources if specified
    enabled_sources = []
    if args.sources:
        enabled_sources = [s.strip().lower() for s in args.sources.split(',')]
    
    # Register Flickr source
    if not enabled_sources or 'flickr' in enabled_sources:
        flickr = FlickrSource(config)
        if flickr.is_available():
            downloader.register_source(flickr)
        else:
            logger.warning("Flickr source not available (check config.ini)")
    
    # Register KonIQ source
    if not enabled_sources or 'koniq' in enabled_sources:
        koniq = KonIQSource(config)
        if koniq.is_available():
            downloader.register_source(koniq)
        else:
            logger.warning("KonIQ source not available (run setup_koniq_dataset.py first)")
    
    # Register Wiki Commons source
    if not enabled_sources or 'wiki' in enabled_sources:
        wiki = WikiCommonsSource(config)
        downloader.register_source(wiki)
    
    # Check if any sources are available
    if not downloader.sources:
        logger.error("No image sources available. Please configure at least one source.")
        logger.info("\nTo get started:")
        logger.info("  1. For Flickr: Add API key to config.ini")
        logger.info("  2. For KonIQ: Run 'python setup_koniq_dataset.py'")
        logger.info("  3. Wiki Commons works without configuration")
        return 1
    
    print(f"Active sources: {', '.join(s.name for s in downloader.sources)}")
    print(f"Output directory: {downloader.output_dir.absolute()}")
    print()
    
    # Determine score distribution
    score_dist = None
    if args.high is not None or args.medium is not None or args.low is not None:
        high = args.high or 0
        medium = args.medium or 0
        low = args.low or 0
        
        total_specified = high + medium + low
        if total_specified != args.count:
            logger.warning(f"Specified distribution ({total_specified}) doesn't match count ({args.count})")
            logger.warning("Using automatic distribution instead")
        else:
            score_dist = {'high': high, 'medium': medium, 'low': low}
            print(f"Distribution: {high} high + {medium} medium + {low} low quality images")
    
    if score_dist is None:
        print(f"Using automatic distribution for {args.count} images")
        print(f"  High (7-10):   ~{int(args.count * 0.3)} images")
        print(f"  Medium (4-7):  ~{int(args.count * 0.4)} images")
        print(f"  Low (0-4):     ~{int(args.count * 0.3)} images")
    
    print()
    print("Starting download...")
    print("-" * 70)
    
    # Download images
    try:
        downloaded = downloader.download_images(args.count, score_dist)
        
        print("-" * 70)
        print()
        print(f"✓ Successfully downloaded {len(downloaded)} images")
        print(f"  Location: {downloader.output_dir.absolute()}")
        
        # Show score distribution of downloaded images
        if downloaded:
            print("\nDownloaded score distribution:")
            high_count = sum(1 for p in downloaded if '7.' in p.name or '8.' in p.name or '9.' in p.name or '10.' in p.name)
            medium_count = sum(1 for p in downloaded if '4.' in p.name or '5.' in p.name or '6.' in p.name)
            low_count = sum(1 for p in downloaded if '0.' in p.name or '1.' in p.name or '2.' in p.name or '3.' in p.name)
            print(f"  High quality:   {high_count} images")
            print(f"  Medium quality: {medium_count} images")
            print(f"  Low quality:    {low_count} images")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\nDownload interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Download failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
