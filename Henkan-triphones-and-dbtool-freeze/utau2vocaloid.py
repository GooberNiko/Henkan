#!/usr/bin/env python3
"""Old name of henkan.py, kept so existing scripts and shortcuts still work."""
import os
import runpy

if __name__ == '__main__':
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'henkan.py'), run_name='__main__')
