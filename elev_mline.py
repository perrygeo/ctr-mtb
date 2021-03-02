import mercantile
from shapely.geometry import LineString, Point
import numpy as np
import os
import rasterio
import json
from pyproj import Transformer
from pyproj.enums import TransformDirection


URL_TEMPLATE = "http://localhost:8090/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif"

# Required for good vsis3 performance
os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tif"
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "TRUE"


def densify_geometry(
    line_geometry,
    step_distance,
    transformer,
):
    assert line_geometry["type"] == "LineString"

    proj_coords = [pt for pt in transformer.itransform(line_geometry["coordinates"])]

    line_shape = LineString(proj_coords)
    length_m = line_shape.length

    # TODO probably dropping last point, corners, etc
    steps = int(length_m / step_distance)
    xys = [
        line_shape.interpolate(d).__geo_interface__["coordinates"]
        for d in np.linspace(0, length_m, steps)
    ]
    assert len(xys) == steps
    return LineString(xys)


def create_ordered_chunks_by_url(geom, zoom):
    chunks = []
    current = None
    for lng, lat in geom["coordinates"]:
        tile = mercantile.tile(lng=lng, lat=lat, zoom=zoom)
        url = URL_TEMPLATE.format(x=tile.x, y=tile.y, z=tile.z)
        merc_coords = mercantile.xy(lng, lat)
        # if a new url is encountered, flush state
        if current and url != current[0]:
            chunks.append(current)
            current = (url, [merc_coords])
        elif current is None:
            current = (url, [merc_coords])
        else:
            current[1].append(merc_coords)
    chunks.append(current)
    return chunks


def profile_geometry(geom, zoom=9):
    final = []
    ocs = create_ordered_chunks_by_url(geom, zoom)

    total = 0
    for oc in ocs:
        total += len(oc[1])
    assert len(geom["coordinates"]) == total

    for url, coords in create_ordered_chunks_by_url(geom, zoom):
        with rasterio.open(url) as ds:
            for (x, y) in coords:
                r, c = ds.index(x, y)
                window = ((int(r), int(r + 1)), (int(c), int(c + 1)))
                src_array = ds.read(window=window)
                val = src_array[0, 0, 0]
                final.append(int(val))
    return final


def make_poi(poi_feature, line, transformer):
    """Determine `m`, their distance along linestring

    return list of {'m': ___, 'category': ___, 'label': ___}
    """
    # Transform to same CRS as line
    proj_coords = list(
        transformer.itransform(
            [poi_feature["geometry"]["coordinates"]],
        )
    )[0]
    pt = Point(proj_coords)
    m = line.project(pt)
    return {"m": m, **poi_feature["properties"]}


def read_data():
    """I/O"""
    geom = {
        "type": "LineString",
        "coordinates": [[-105.1, 40.1], [-105.2, 40.2]],
    }
    orig_feature = {"type": "Feature", "geometry": geom}
    poi_features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-105.15, 40.15]},
            "properties": {"category": "restaurant", "label": "Dennys"},
        },
    ]
    segment_features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-105.14, 40.14]},
            "properties": {"category": "section", "label": "first-part"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-105.2, 40.2]},
            "properties": {"category": "section", "label": "last-part"},
        },
    ]
    return orig_feature, poi_features, segment_features


def read_ctr_data():
    path = "data/ctr-track-master.geojson"
    with open(path) as fh:
        fc = json.loads(fh.read())

    # grab coords only, convert multilinestring to linestring
    coords = fc["features"][0]["geometry"]["coordinates"][0]
    line_feature = {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
    }
    middle = int(len(coords) / 2.0)
    poi_features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords[middle]},
            "properties": {"category": "road crossing", "label": "first section"},
        },
    ]

    path = "data/ctr-segment-points.geojson"
    with open(path) as fh:
        fc = json.loads(fh.read())
    segment_features = [f for f in fc["features"] if f["geometry"]]
    # TODO start point?

    # return read_data()
    return line_feature, poi_features, segment_features


def get_bounds(coords):
    ls = LineString(coords)
    return ls.bounds


if __name__ == "__main__":
    # Inputs
    orig_feature, poi_features, segment_features = read_ctr_data()

    # 4087 is World Equidistant Cylindrical
    transformer = Transformer.from_crs(4326, 4087, always_xy=True)
    # Web mercator
    transformer = Transformer.from_crs(4326, 3857, always_xy=True)
    # "World Equidistant Cylindrical (Sphere)
    transformer = Transformer.from_crs(4326, 3786, always_xy=True)
    step = 50  # meters
    line = densify_geometry(orig_feature["geometry"], step, transformer)

    # POIs
    # list of {'m': ___, 'category': ___, 'label': ___ }
    #
    pois = [make_poi(f, line, transformer) for f in poi_features]

    # Segments
    # list of {'m': ___, 'category': ___, 'label': ___ }
    #
    # Segments are defined by their ENDING coordinate to facilitate data input
    # ie it's easier to place a point than draw the linestring of an entire segment.
    # perhaps a pair of points? there are edge cases galore... stick to points on a line for now
    # That means we need to define segments explicitly as None if there is no label
    segments = [make_poi(f, line, transformer) for f in segment_features]

    # Measures
    # list of {'m': ___, 'y': ___}
    #
    lons, lats = transformer.transform(*line.xy, direction=TransformDirection.INVERSE)
    new_coords = list(zip(lons, lats))
    elevations = profile_geometry({"type": "LineString", "coordinates": new_coords})
    assert len(new_coords) == len(elevations)

    # need to adjust elevation m values based on the distance bias
    scale_factor = line.length / ((len(elevations) - 1) * step)
    distances = [(i * step * scale_factor) for i in range(len(new_coords))]
    elevs = [{"m": d, "z": e} for d, e in zip(distances, elevations)]
    mpoints = [{"m": d, "coords": pt} for d, pt in zip(distances, new_coords)]

    data = {
        "bounds": LineString(new_coords).bounds,
        "line_feature": orig_feature,
        "poi_features": poi_features,
        "segment_features": segment_features,
        "mpoints": mpoints,
        "melevations": elevs,
        "mpois": pois,
        "msegments": segments,
    }
    print(json.dumps(data, indent=2))
    # print(len(elevations))
    # # Confirm line manually in QGIS
    # d = {
    #     "type": "Feature",
    #     "geometry": {"type": "LineString", "coordinates": new_coords},
    # }
    # print(json.dumps(d))
