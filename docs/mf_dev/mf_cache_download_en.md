# PiFinder Offline Cache Download Guide

[English](mf_cache_download_en.md) | [한국어](mf_cache_download_ko.md)

## Purpose and scope

`scripts/warm_pifinder_caches.py` prepares rebuildable local caches while the
PiFinder has internet access. It makes catalog browsing and catalog-detail
pages faster and keeps cached object images available when the PiFinder is
offline.

The command creates or downloads only the following data:

| Location | Content | Purpose |
| --- | --- | --- |
| `~/PiFinder_data/cache/hip_main.pkl` | Parsed Hipparcos star catalog | Faster star-field startup |
| `~/PiFinder_data/cache/hip_bv.npz` | Hipparcos B-V color-index lookup | Faster SQM color-correction startup |
| `~/PiFinder_data/cache/catalogs/` | Composite-object catalog cache | Faster catalog search and list startup |
| `~/PiFinder_data/catalog_images/` | POSS/SDSS survey images | Object pictures in PiFinder and the web catalog |

It does not change observing records, equipment settings, Wi-Fi credentials,
user photos, or logs. It also does not pre-create condition-specific data such
as a camera warm-pixel map.

## Before you start

- The **PiFinder itself** must have internet access. A phone connected to
  PiFinderAP does not necessarily provide internet access to the PiFinder.
- Leave enough power and storage available. The prebuilt image's 13,000+
  catalog images occupy about 5 GB; allow **at least 6 GB free** for the full
  POSS+SDSS download. The final size varies with the current catalog and
  survey responses.
- Do not run this during an observing session. Cache generation uses CPU,
  network bandwidth, and SD-card I/O.

## Default command

From the repository root, run:

```bash
cd /home/pifinder/PiFinder
python3 scripts/warm_pifinder_caches.py
```

The default sequence is:

1. Build the Hipparcos star-field and B-V color-index caches.
2. Build the complete composite-object catalog cache.
3. Download POSS and SDSS survey images through `PiFinder.gen_images`.

Ten images are downloaded concurrently by default. Use a lower worker count
on an unreliable or shared network:

```bash
python3 scripts/warm_pifinder_caches.py --workers 4
```

## Choose the cache scope

PiFinder and the web catalog use POSS images. To avoid storing SDSS images,
download only POSS:

```bash
python3 scripts/warm_pifinder_caches.py --images poss
```

To build only the fast-start local caches, without images:

```bash
python3 scripts/warm_pifinder_caches.py --images none
```

To download images without rebuilding the runtime caches:

```bash
python3 scripts/warm_pifinder_caches.py --skip-runtime
```

## Check progress and completion

The command prints each runtime-cache step and the image-download progress.
In another terminal, inspect the stored size and file counts with:

```bash
du -sh ~/PiFinder_data/cache ~/PiFinder_data/catalog_images
find ~/PiFinder_data/catalog_images -name '*_POSS.jpg' | wc -l
find ~/PiFinder_data/catalog_images -name '*_SDSS.jpg' | wc -l
```

`Cache warm-up complete` indicates success. Afterwards, disconnect internet
access and open a catalog detail page to verify that a cached object image is
still shown.

## Stop and resume

Press `Ctrl-C` to stop. When internet access is available again, run the same
command to continue: `gen_images` skips image files that already exist, and
valid runtime caches are reused.

The cache command stops its temporary planet/comet refresh timers before it
exits. After `Cache warm-up complete` appears, the shell prompt should return
immediately.

An abrupt power loss during a file write can leave the image currently being
written incomplete. If one object image consistently fails to display, remove
only that object's `*_POSS.jpg` or `*_SDSS.jpg` file and run the cache command
again. Do not remove settings or observing records.

## Troubleshooting

| Symptom | Check and action |
| --- | --- |
| No image files are added | Confirm internet, DNS, and HTTPS access on the PiFinder itself. |
| Storage becomes full | Use `--images poss` or use larger storage. |
| Download is too slow | Adjust `--workers` between 4 and 10; lower values can be more reliable on weak networks. |
| A web catalog detail page still has no image | That object may not have a POSS survey image. The web server always prefers an existing local cache file. |

## Implementation references

- Runner: `scripts/warm_pifinder_caches.py`
- Image generator: `python/PiFinder/gen_images.py`
- Web catalog image route: `python/PiFinder/web_catalogs.py`
- Cache path definitions: `python/PiFinder/utils.py`
