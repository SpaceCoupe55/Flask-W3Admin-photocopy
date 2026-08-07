"""Static pages carried over from the W3Admin template.

The template's demo modules (e-commerce, blog, chat, email, UI kit and the four
sample dashboards) are no longer routed — they were retail/showcase pages with
no place in a leasing back office. Their source files are still in
`templates/w3admin/` if a component ever needs to be lifted from them. What is
kept here are the error pages, which the app genuinely uses.
"""

from flask import Blueprint, render_template

main = Blueprint("main", __name__)


@main.route("/errors/400")
def page_error_400():
    return render_template("400.html"), 400


@main.route("/errors/403")
def page_error_403():
    return render_template("403.html"), 403


@main.route("/errors/404")
def page_error_404():
    return render_template("404.html"), 404


@main.route("/errors/500")
def page_error_500():
    return render_template("500.html"), 500


@main.route("/errors/503")
def page_error_503():
    return render_template("503.html"), 503
