import boto3
import json
import gzip
from botocore.exceptions import ClientError
from config import Config

class S3Manager:
    """Manages large data storage in S3 with compression."""
    
    def __init__(self, bucket_name=None):
        # Use Config.S3_BUCKET if not explicitly provided
        self.bucket_name = bucket_name or Config.S3_BUCKET
        self.s3_client = boto3.client('s3', region_name=Config.AWS_REGION)
        print(f"--- S3Manager initialized with bucket: {self.bucket_name} in region: {Config.AWS_REGION} ---")
    
    def save_large_data(self, athlete_id, data_type, data):
        """
        Saves large data to S3 with gzip compression.
        
        Args:
            athlete_id: The athlete's ID
            data_type: Type of data (e.g., 'garmin_history_raw')
            data: Python dict/list to save
        
        Returns:
            str: The S3 key where data was saved, or None on failure
        """
        s3_key = f"athletes/{athlete_id}/{data_type}.json.gz"
        
        try:
            # Convert to JSON and compress
            json_bytes = json.dumps(data, default=str).encode('utf-8')
            compressed = gzip.compress(json_bytes)
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=compressed,
                ContentType='application/json',
                ContentEncoding='gzip'
            )
            
            print(f"S3: Saved {len(compressed)/1024:.1f} KB to s3://{self.bucket_name}/{s3_key}")
            return s3_key
            
        except ClientError as e:
            print(f"S3 ERROR saving {s3_key}: {e}")
            return None
        except Exception as e:
            print(f"S3 ERROR (unexpected) saving {s3_key}: {e}")
            return None
    
    def load_large_data(self, s3_key):
        """
        Loads and decompresses data from S3.
        
        Args:
            s3_key: Full S3 key (e.g., 'athletes/123/garmin_history_raw.json.gz')
        
        Returns:
            dict/list: The loaded data, or None on failure
        """
        try:
            # Download from S3
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            # Decompress and parse
            compressed = response['Body'].read()
            json_bytes = gzip.decompress(compressed)
            data = json.loads(json_bytes.decode('utf-8'))
            
            print(f"S3: Loaded {len(compressed)/1024:.1f} KB from s3://{self.bucket_name}/{s3_key}")
            return data
            
        except self.s3_client.exceptions.NoSuchKey:
            print(f"S3: Key not found: {s3_key}")
            return None
        except ClientError as e:
            print(f"S3 ERROR loading {s3_key}: {e}")
            return None
        except Exception as e:
            print(f"S3 ERROR (unexpected) loading {s3_key}: {e}")
            return None
    
    def delete_large_data(self, s3_key):
        """
        Deletes data from S3.
        
        Args:
            s3_key: Full S3 key to delete
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            print(f"S3: Deleted s3://{self.bucket_name}/{s3_key}")
            return True
            
        except ClientError as e:
            print(f"S3 ERROR deleting {s3_key}: {e}")
            return False


# Initialize singleton instance
try:
    s3_manager = S3Manager()
    S3_AVAILABLE = True
    print("✅ S3Manager initialized successfully")
except Exception as e:
    print(f"⚠️  S3Manager initialization failed: {e}")
    s3_manager = None
    S3_AVAILABLE = False