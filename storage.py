"""
Data storage module for CSV export
"""
import csv
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
from config import DATA_DIR, CSV_COLUMNS, CSV_APPEND_MODE

class CSVStorage:
    """Handles CSV file operations for trade data"""
    
    def __init__(self):
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(exist_ok=True)
    
    def get_filename(self, ticker: str, from_date: str, until_date: str) -> Path:
        """Generate CSV filename for ticker and date range"""
        # using single file per ticker for date range
        filename = f"{ticker}_{from_date}_{until_date}.csv"
        return self.data_dir / filename
    
    def get_daily_filename(self, ticker: str, date: str) -> Path:
        """Generate CSV filename for ticker and single date"""
        filename = f"{ticker}_{date}.csv"
        return self.data_dir / filename
    
    def save_trades(
        self,
        ticker: str,
        date: str,
        trades: List[Dict[str, Any]],
        filename: Path = None,
        append: bool = CSV_APPEND_MODE
    ) -> Dict[str, Any]:
        """
        Save trade data to CSV
        
        Args:
            ticker: Stock symbol
            date: Date string
            trades: List of trade dicts
            filename: Optional specific filename to use
            append: Whether to append or overwrite
        
        Returns:
            Dict with success status and info
        """
        if not trades:
            return {
                'success': True,
                'message': 'No trades to save',
                'rows_written': 0
            }
        
        # use provided filename or generate daily filename
        if filename is None:
            filename = self.get_daily_filename(ticker, date)
        
        try:
            # check if file exists
            file_exists = filename.exists()
            mode = 'a' if (append and file_exists) else 'w'
            
            with open(filename, mode, newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
                
                # write header if new file or overwrite mode
                if mode == 'w' or not file_exists:
                    writer.writeheader()
                
                # write rows
                rows_written = 0
                for trade in trades:
                    # add date field
                    trade['date'] = date
                    
                    # clean price and change fields (remove commas)
                    if 'price' in trade:
                        trade['price'] = str(trade['price']).replace(',', '')
                    if 'change' in trade:
                        trade['change'] = str(trade['change']).replace('%', '').replace('+', '')
                    
                    # write row
                    writer.writerow(trade)
                    rows_written += 1
            
            return {
                'success': True,
                'filename': str(filename),
                'rows_written': rows_written,
                'mode': mode
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': f'Failed to write CSV: {str(e)}'
            }
    
    def list_output_files(self) -> List[Dict[str, Any]]:
        """List all CSV files in data directory"""
        files = []
        for filepath in self.data_dir.glob('*.csv'):
            stat = filepath.stat()
            files.append({
                'filename': filepath.name,
                'path': str(filepath),
                'size_bytes': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
        
        # sort by modified time desc
        files.sort(key=lambda x: x['modified'], reverse=True)
        return files
    
    def get_file_path(self, filename: str) -> Path:
        """Get full path for a filename"""
        return self.data_dir / filename

