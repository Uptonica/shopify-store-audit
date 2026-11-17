# Shopify Store Audit for Claude Code

A free, open source skill for Claude Code. Give it the address of a Shopify
store and it tells you what Google, AI assistants and shoppers miss on your
product pages, and what to fix first.

It only reads public pages. It never logs in and never changes your store.

## Install

In Claude Code:

```
/plugin marketplace add uptonica/shopify-store-audit
/plugin install shopify-audit@uptonica
```

Then ask:

```
Audit my Shopify store: https://www.example.com
```

## What it checks

| Area | Checks |
| --- | --- |
| AI assistants | Product data that ChatGPT, Claude, Perplexity and Gemini can read: price, availability, brand, product code, review stars. Whether your robots.txt blocks their crawlers. |
| Google | Titles and meta descriptions, share image, canonical address, sitemap, photo alt text, duplicate product titles, collection descriptions. |
| Product pages | Products with one photo or none, short or empty descriptions, missing SKUs. |
| Markets | Which languages the store declares. |
| Customers | Whether shoppers can reach you by chat or WhatsApp from the home page. |
| Apps | Email, reviews, loyalty, search, upsell and support apps found on the home page. |

## What you get

A short report in your language:

1. The five fixes that matter most, each with the numbers, example products and
   the steps in the Shopify admin.
2. What is already good.
3. What could not be measured, and why.

Every number comes from your store's pages. Nothing is estimated.

## Limits

- Many stores limit automated visits to product pages. When that happens,
  Claude opens two or three product pages itself and says which ones.
- Languages are read from what the store declares; whether each page is really
  translated is not measured.
- Apps loaded after the cookie banner, or only on other pages, may not appear.

## Run it without Claude

```
python3 skills/shopify-audit/scripts/audit.py https://www.example.com
```

Python 3.9 or later, standard library only. The output is JSON.

## Made by Uptonica

[Uptonica](https://uptonica.com/?utm_source=github&utm_medium=referral&utm_campaign=shopify-audit-skill)
writes product descriptions and translations with AI and sends them back to
Shopify. [See your store analysed by Uptonica, free](https://app.uptonica.com/audit?utm_source=github&utm_medium=referral&utm_campaign=shopify-audit-skill).

MIT License.
