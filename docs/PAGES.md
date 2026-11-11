# Project website

The GitHub Pages site lives in `site/` and is published at
[2026 onn 4K Pro unlock and root](https://openensemble.github.io/onn-4k-pro-2026-unlock-root/).

Keep its supported firmware and claims aligned with `profile.json`, `README.md`
and `docs/RESULTS.md`. Hardware results and simulated guide checks are described
separately. This is the 2026 `jarvis2` project; the 2024 model has its own site.

The page uses static HTML, CSS and a small clipboard script. There are no runtime
dependencies, external fonts, analytics, device connections or downloaded firmware
in the website. Content, links and FAQ disclosures work without JavaScript.
The device photo is the existing Walmart identification image copied into
`site/assets/`, with credit on the page. Donation addresses match the README.

## Local preview

From the repository root:

```sh
python3 -m http.server 5214 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:5214/`. Stop the server when finished. Asset paths are
relative so the page works under GitHub Pages' repository subpath.

## Publishing

GitHub Pages uses GitHub Actions as its publishing source. The **Deploy project
website** workflow uploads only `site/`, then deploys it with HTTPS. Its action
versions are pinned to the reviewed upstream commits. It runs for website or
workflow changes on `main` and supports manual runs from the Actions tab.

Commit and push changes only when authorized. After deployment, check the public
page at desktop and mobile widths, command and wallet copy buttons, FAQ disclosures,
image loading and links. Clipboard access can depend on browser permissions; a
manual text-selection fallback is provided.

If the website URL changes, update the canonical URL, social metadata, JSON-LD,
sitemap, README link and repository About website field together.
