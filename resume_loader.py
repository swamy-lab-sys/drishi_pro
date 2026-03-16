"""
Resume and Job Description Loader with Caching

Features:
- Module-level cache with mtime tracking
- Auto-reload when file changes
- Explicit cache invalidation function
"""

import os
from pathlib import Path

# Module-level cache
_resume_cache = {
    'content': None,
    'mtime': 0,
    'path': None
}

_jd_cache = {
    'content': None,
    'mtime': 0,
    'path': None
}


def invalidate_resume_cache():
    """Invalidate the resume cache, forcing a reload on next access."""
    global _resume_cache
    _resume_cache = {
        'content': None,
        'mtime': 0,
        'path': None
    }


def invalidate_jd_cache():
    """Invalidate the job description cache, forcing a reload on next access."""
    global _jd_cache
    _jd_cache = {
        'content': None,
        'mtime': 0,
        'path': None
    }


def load_resume(path):
    """
    Load resume from file with caching.

    Returns cached content if file hasn't changed.
    Auto-reloads if file modified.
    """
    global _resume_cache

    path = str(path)

    try:
        # Check if file exists
        if not os.path.exists(path):
            return ""

        # Get current mtime
        current_mtime = os.path.getmtime(path)

        # Return cache if valid
        if (_resume_cache['path'] == path and
            _resume_cache['mtime'] == current_mtime and
            _resume_cache['content'] is not None):
            return _resume_cache['content']

        # Load fresh content
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Update cache
        _resume_cache = {
            'content': content,
            'mtime': current_mtime,
            'path': path
        }

        return content

    except (FileNotFoundError, IOError, OSError):
        return ""


def load_job_description(path):
    """
    Load job description from file with caching.

    Returns cached content if file hasn't changed.
    Auto-reloads if file modified.
    """
    global _jd_cache

    path = str(path)

    try:
        # Check if file exists
        if not os.path.exists(path):
            return ""

        # Get current mtime
        current_mtime = os.path.getmtime(path)

        # Return cache if valid
        if (_jd_cache['path'] == path and
            _jd_cache['mtime'] == current_mtime and
            _jd_cache['content'] is not None):
            return _jd_cache['content']

        # Load fresh content
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Update cache
        _jd_cache = {
            'content': content,
            'mtime': current_mtime,
            'path': path
        }

        return content

    except (FileNotFoundError, IOError, OSError):
        return ""


def get_resume_info(path):
    """Get resume file info for debugging."""
    path = str(path)
    try:
        if os.path.exists(path):
            stat = os.stat(path)
            return {
                'exists': True,
                'size': stat.st_size,
                'mtime': stat.st_mtime,
                'cached': _resume_cache['path'] == path and _resume_cache['content'] is not None
            }
    except:
        pass
    return {'exists': False, 'size': 0, 'mtime': 0, 'cached': False}
