# TODO

## Server landing page

- [x] Redesign `templates/index.html` as a real landing page instead of a plain public page list
- [x] Rebuild local `pages/index.html` from the new landing page template
- [x] Review the new landing page copy and visual rhythm once more
- [x] Deploy the new landing page to the production server (slug: xuans-pagegate)
- [x] Verify the production homepage after deploy
- [ ] Sync templates/index.html and pages/index.html to remote server when ready

## Product philosophy follow-up

- [ ] Clarify the product-layer vs instance-layer messaging
  - Product: `PageGate`
  - Instance: `Xuan & Friends' PageGate`
- [x] Introduce per-user routes with `/<username>` and `/<username>/<slug>/`
- [ ] Define what the global homepage should represent if every user gets their own PageGate

## Branding follow-up

- [x] Introduce configurable `branding.product_name` / `branding.instance_name` instead of hardcoding branding in templates
- [ ] Decide which pages should use instance branding vs product branding
  - homepage
  - login page
  - pending page
  - dashboard
  - public landing / marketing pages
