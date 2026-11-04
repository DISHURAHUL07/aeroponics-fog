"""
Streamlit Community Cloud Entrypoint
====================================
Automatically launches dashboard.py for Streamlit Cloud deployment.
"""

import os
import sys

# Ensure project directory is in Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Execute main dashboard
import dashboard
