from setuptools import setup

from django_profiler import __version__


description = """
Helpers for aggregating across tables.
"""

setup(
    name = "django-polymorphic-queries",
    url = "https://github.com/eshares/django-polymorphic-queries",
    author = "eShares inc.",
    author_email = "dev@esharesinc.com",
    version=__version__,
    packages = [
        "polymorphic"
    ],
    description = description.strip(),
    install_requires=[
        "django>=1.9.0",
    ],
    zip_safe=False,
    include_package_data = True,
    package_data = {
        "": ["*.md"],
    },
    classifiers = [
    ],
)