from flask import Blueprint, render_template
from utils.decorators import login_required

help_bp = Blueprint('help', __name__)

@help_bp.route('/help')
@login_required
def help():
    """Help & Getting Started page"""
    return render_template('help.html')

# Future routes ready to add:
# @help_bp.route('/help/faq')
# @help_bp.route('/help/tutorials')
# @help_bp.route('/help/contact')