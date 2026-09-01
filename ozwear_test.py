name: Test OZWear API

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test-ozwear-api:
    runs-on: ubuntu-latest

    steps:
      - name: Download repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install packages
        run: pip install -r requirements.txt

      - name: Test OZWear API
        env:
          OZWEAR_API_KEY: ${{ secrets.OZWEAR_API_KEY }}
          OZWEAR_API_SECRET: ${{ secrets.OZWEAR_API_SECRET }}
          OZWEAR_API_URL: https://api.ozwearugg.net/rest/s1/openapi/products
        run: python ozwear_test.py

      - name: Save API result
        if: success()
        uses: actions/upload-artifact@v4
        with:
          name: ozwear-products
          path: ozwear_products.json
          retention-days: 3
