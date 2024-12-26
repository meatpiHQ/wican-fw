@echo off
.\minhtml.exe --output main/homepage.html --keep-closing-tags --minify-css --minify-js main/homepage_full.html
echo HTML minification completed successfully!
