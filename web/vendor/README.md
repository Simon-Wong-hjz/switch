# Bundled Markdown renderer

markdown-it 15.0.2, MIT. The browser UMD build is bundled locally; no package manager or external script request is needed at runtime.

- Source: https://github.com/markdown-it/markdown-it/tree/15.0.2
- Distribution: https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.2.tgz
- File: `package/dist/browser/markdown-it.umd.min.js`, saved here as `markdown-it.min.js`.
- License: `markdown-it.LICENSE`; bundled dependency licenses and notices are preserved in [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES).
- SHA-256: `635972b985228e8af9f0143647c68616b7a3bb09f6946e7e4a52e43dcf5e7be5`.

The downloaded package was checked against its registry SHA-512 integrity value. Rendering options and link behavior are set in `../reply-reader.js`: raw HTML is escaped, links are limited to HTTP(S), and images are presented as links rather than fetched automatically.

Bundled dependency sources were matched against the upstream source map: entities 8.0.0 (BSD-2-Clause), linkify-it 6.0.0 (MIT), mdurl 2.1.0 (MIT), punycode.js 2.3.1 (MIT), and uc.micro 3.0.0 (MIT). The distributed bundle remains unmodified.
