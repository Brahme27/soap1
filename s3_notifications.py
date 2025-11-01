import json
import os
from datetime import datetime
import streamlit as st


def get_tracked_files_path():
    """Get path to tracked files JSON file"""
    return os.path.join(os.path.dirname(__file__), '.s3_tracked_files.json')


def load_tracked_files():
    """Load previously tracked S3 files from local storage"""
    tracked_path = get_tracked_files_path()
    if os.path.exists(tracked_path):
        try:
            with open(tracked_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_tracked_files(tracked_data):
    """Save tracked S3 files to local storage"""
    tracked_path = get_tracked_files_path()
    try:
        with open(tracked_path, 'w') as f:
            json.dump(tracked_data, f, indent=2)
    except IOError as e:
        st.warning(f"Could not save tracked files: {e}")


def extract_folders_from_files(files_list):
    """
    Extract unique folder names from file paths
    
    Args:
        files_list: List of file paths
    
    Returns:
        Set of folder names
    """
    folders = set()
    for file_path in files_list:
        # Get the first part of the path (folder name)
        parts = file_path.split('/')
        if len(parts) > 1:
            folders.add(parts[0])
    return folders


def check_new_files(bucket_name, current_files):
    """
    Check for new files and folders in S3 bucket and return new items with notification
    
    Args:
        bucket_name: S3 bucket name
        current_files: List of current files in bucket
    
    Returns:
        Tuple of (new_files_list, new_folders_list, notification_message)
    """
    tracked_data = load_tracked_files()
    bucket_key = f"bucket_{bucket_name}"
    
    # Get previously tracked files and folders for this bucket
    previous_files = set(tracked_data.get(bucket_key, {}).get('files', []))
    previous_folders = set(tracked_data.get(bucket_key, {}).get('folders', []))
    
    current_files_set = set(current_files)
    current_folders = extract_folders_from_files(current_files)
    
    # Find new files and folders
    new_files = list(current_files_set - previous_files)
    new_folders = list(current_folders - previous_folders)
    
    # Update tracked files and folders
    if bucket_key not in tracked_data:
        tracked_data[bucket_key] = {}
    
    tracked_data[bucket_key]['files'] = current_files
    tracked_data[bucket_key]['folders'] = list(current_folders)
    tracked_data[bucket_key]['last_checked'] = datetime.now().isoformat()
    tracked_data[bucket_key]['new_files_count'] = len(new_files)
    tracked_data[bucket_key]['new_folders_count'] = len(new_folders)
    
    save_tracked_files(tracked_data)
    
    return new_files, new_folders, tracked_data[bucket_key]


def display_new_file_notification(new_files, new_folders, bucket_info):
    """
    Display notification/reminder for new files and folders found in S3
    
    Args:
        new_files: List of newly detected files
        new_folders: List of newly detected folders
        bucket_info: Dictionary with bucket metadata
    """
    if new_files or new_folders:
        notification_text = "🆕 **New Items Found in S3!**\n\n"
        
        # Display new folders
        if new_folders:
            notification_text += f"📁 **New Folders ({len(new_folders)}):**\n"
            for folder in sorted(new_folders)[:5]:
                notification_text += f"- 📂 `{folder}`\n"
            if len(new_folders) > 5:
                notification_text += f"...and {len(new_folders) - 5} more folder(s)\n"
            notification_text += "\n"
        
        # Display new files
        if new_files:
            notification_text += f"📄 **New Files ({len(new_files)}):**\n"
            for file in new_files[:5]:
                notification_text += f"- 📄 `{file}`\n"
            if len(new_files) > 5:
                notification_text += f"...and {len(new_files) - 5} more file(s)\n"
        
        notification_text += f"\n**Last checked:** {bucket_info.get('last_checked', 'N/A')}"
        
        st.success(notification_text)
        return True
    
    return False


def get_file_summary(bucket_name, files_list):
    """
    Get summary statistics about files in bucket
    
    Args:
        bucket_name: S3 bucket name
        files_list: List of files in bucket
    
    Returns:
        Dictionary with file statistics
    """
    tracked_data = load_tracked_files()
    bucket_key = f"bucket_{bucket_name}"
    bucket_info = tracked_data.get(bucket_key, {})
    
    summary = {
        'total_files': len(files_list),
        'new_files': len(files_list) - len(bucket_info.get('files', [])),
        'last_checked': bucket_info.get('last_checked', 'Never'),
        'bucket_name': bucket_name
    }
    
    return summary


def create_reminder_notification(bucket_name, new_files_count, new_folders_count=0):
    """
    Create a reminder notification message for DBT/clinical purposes
    
    Args:
        bucket_name: S3 bucket name
        new_files_count: Number of new files
        new_folders_count: Number of new folders
    
    Returns:
        Formatted reminder message
    """
    if new_files_count == 0 and new_folders_count == 0:
        return None
    
    items_text = []
    if new_folders_count > 0:
        items_text.append(f"{new_folders_count} new folder(s)")
    if new_files_count > 0:
        items_text.append(f"{new_files_count} new file(s)")
    
    items_summary = " and ".join(items_text)
    
    reminder = f"""
    ⏰ **Reminder: New Session Items Available**
    
    📊 **Bucket:** `{bucket_name}`
    📈 **New Items:** {items_summary}
    ⏱️ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    
    💡 **Action:** Review and load new session folders for analysis
    """
    
    return reminder


def reset_tracked_files(bucket_name=None):
    """
    Reset tracked files for a specific bucket or all buckets
    
    Args:
        bucket_name: Specific bucket to reset, or None for all
    """
    tracked_data = load_tracked_files()
    
    if bucket_name:
        bucket_key = f"bucket_{bucket_name}"
        if bucket_key in tracked_data:
            del tracked_data[bucket_key]
    else:
        tracked_data = {}
    
    save_tracked_files(tracked_data)
