# CTR-MTB

Colorado Trail Race map.

https://perrygeo.github.io/ctr-mtb/


## Running

Install `varnish` and run as a proxy to cache the elevation tiles

```bash
run-cache-proxy.sh
# type "start"
```

Data is processed using Python

```bash
python elev_mline.py > data/ctr-mline.json
```

Data is read by the Javascript client, served locally

```bash
python -m http.server
```