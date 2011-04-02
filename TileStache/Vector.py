""" Provider that returns vector representation of features in a data source.

This is a provider that does not return an image, but rather queries
a data source for raw features and replies with a vector representation
such as GeoJSON. For example, it's possible to retrieve data for
locations of OpenStreetMap points of interest or street centerlines
contained within a tile's boundary.

Many Polymaps (http://polymaps.org) examples use GeoJSON vector data tiles,
which can be effectively created using this provider.

Vector functionality is provided by OGR (http://www.gdal.org/ogr/).
Thank you, Frank Warmerdam.

Currently two serializations and three encodings are supported for a total
of six possible kinds of output with these tile name extensions:

  GeoJSON (.geojson):
    See http://geojson.org/geojson-spec.html

  Arc GeoServices JSON (.arcjson):
    See http://www.esri.com/library/whitepapers/pdfs/geoservices-rest-spec.pdf
  
  GeoBSON (.geobson) and Arc GeoServices BSON (.arcbson):
    BSON-encoded GeoJSON and Arc JSON, see http://bsonspec.org/#/specification
  
  GeoAMF (.geoamf) and Arc GeoServices AMF (.arcamf):
    AMF0-encoded GeoJSON and Arc JSON, see:
    http://opensource.adobe.com/wiki/download/attachments/1114283/amf0_spec_121207.pdf

Possible future supported formats might include KML and others. Get in touch
via Github to suggest other formats: http://github.com/migurski/TileStache.

Common parameters:

  driver:
    String used to identify an OGR driver. Currently, only "ESRI Shapefile",
    "PostgreSQL", and "GeoJSON" are supported as data source drivers, with
    "postgis" and "shapefile" accepted as synonyms. Not case-sensitive.
    
    OGR's complete list of potential formats can be found here:
    http://www.gdal.org/ogr/ogr_formats.html. Feel free to get in touch via
    Github to suggest new formats: http://github.com/migurski/TileStache.
  
  parameters:
    Dictionary of parameters for each driver.
    
    PostgreSQL:
    "dbname" parameter is required, with name of database.
    "host", "user", and "password" are optional connection parameters.
    One of "table" or "query" is required, with a table name in the first
    case and a complete SQL query in the second.
    
    Shapefile and GeoJSON:
    "file" parameter is required, with filesystem path to data file.
  
  properties:
    Optional list or dictionary of case-sensitive output property names.
    
    If omitted, all fields from the data source will be included in response.
    If a list, treated as a whitelist of field names to include in response.
    If a dictionary, treated as a whitelist and re-mapping of field names.
  
  clipped:
    Default is true.
    Boolean flag for optionally clipping the output geometries to the
    bounds of the enclosing tile. This results in incomplete geometries,
    dramatically smaller file sizes, and improves performance and
    compatibility with Polymaps (http://polymaps.org).
  
  projected:
    Default is false.
    Boolean flag for optionally returning geometries in projected rather than
    geographic coordinates. Typically this means EPSG:900913 a.k.a. spherical
    mercator projection. Stylistically a poor fit for GeoJSON, but useful
    when returning Arc GeoServices responses.
  
  verbose:
    Default is false.
    Boolean flag for optionally expanding output with additional whitespace
    for readability. Results in larger but more readable GeoJSON responses.

Example TileStache provider configuration:

  "vector-postgis-points":
  {
    "provider": {"name": "vector", "driver": "PostgreSQL",
                 "parameters": {"dbname": "geodata", "user": "geodata",
                                "table": "planet_osm_point"}}
  }
  
  "vector-postgis-lines":
  {
    "provider": {"name": "vector", "driver": "postgis",
                 "parameters": {"dbname": "geodata", "user": "geodata",
                                "table": "planet_osm_line"}}
  }
  
  "vector-shapefile-points":
  {
    "provider": {"name": "vector", "driver": "ESRI Shapefile",
                 "parameters": {"file": "oakland-uptown-point.shp"},
                 "properties": ["NAME", "HIGHWAY"]}
  }
  
  "vector-shapefile-lines":
  {
    "provider": {"name": "vector", "driver": "shapefile",
                 "parameters": {"file": "oakland-uptown-line.shp"},
                 "properties": {"NAME": "name", "HIGHWAY": "highway"}}
  }
  
  "vector-postgis-query":
  {
    "provider": {"name": "vector", "driver": "PostgreSQL",
                 "parameters": {"dbname": "geodata", "user": "geodata",
                                "query": "SELECT osm_id, name, highway, way FROM planet_osm_line WHERE SUBSTR(name, 1, 1) = '1'"}}
  }
  
  "vector-sf-streets":
  {
    "provider": {"name": "vector", "driver": "GeoJSON",
                 "parameters": {"file": "stclines.json"},
                 "properties": ["STREETNAME"]}
  }

Caveats:

Your data source must have a valid defined projection, or OGR will not know
how to correctly filter and reproject it. Although response tiles are typically
in web (spherical) mercator projection, the actual vector content of responses
is unprojected back to plain WGS84 latitude and longitude.

If you are using PostGIS and spherical mercator a.k.a. SRID 900913,
you can save yourself a world of trouble by using this definition:
  http://github.com/straup/postgis-tools/raw/master/spatial_ref_900913-8.3.sql
"""

from re import compile
from operator import add
from urlparse import urlparse, urljoin

try:
    from json import JSONEncoder, loads as json_loads
except ImportError:
    from simplejson import JSONEncoder, loads as json_loads

try:
    from osgeo import ogr, osr
except ImportError:
    # At least we'll be able to build the documentation.
    pass

from Core import KnownUnknown
from Geography import getProjectionByName

class VectorResponse:
    """ Wrapper class for Vector response that makes it behave like a PIL.Image object.
    
        TileStache.getTile() expects to be able to save one of these to a buffer.
        
        Constructor arguments:
        - content: Vector data to be serialized, typically a dictionary.
        - verbose: Boolean flag to expand response for better legibility.
    """
    def __init__(self, content, verbose):
        self.content = content
        self.verbose = verbose

    def save(self, out, format):
        """
        """
        #
        # Serialize
        #
        if format == 'WKT':
            if 'wkt' in self.content['crs']:
                out.write(self.content['crs']['wkt'])
            else:
                out.write(_sref_4326().ExportToWkt())
            
            return
        
        if format in ('GeoJSON', 'GeoBSON', 'GeoAMF'):
            content = self.content
            
            if 'wkt' in content['crs']:
                content['crs'] = {'type': 'link', 'properties': {'href': '0.wkt', 'type': 'ogcwkt'}}
            else:
                del content['crs']

        elif format in ('ArcJSON', 'ArcBSON', 'ArcAMF'):
            content = _reserialize_to_arc(self.content)
        
        else:
            raise KnownUnknown('Vector response only saves .geojson, .arcjson, .geobson, .arcbson, .geoamf, .arcamf and .wkt tiles, not "%s"' % format)

        #
        # Encode
        #
        if format in ('GeoJSON', 'ArcJSON'):
            indent = self.verbose and 2 or None
            
            encoded = JSONEncoder(indent=indent).iterencode(content)
            float_pat = compile(r'^-?\d+\.\d+$')
    
            for atom in encoded:
                if float_pat.match(atom):
                    out.write('%.6f' % float(atom))
                else:
                    out.write(atom)
        
        elif format in ('GeoBSON', 'ArcBSON'):
            import bson

            encoded = bson.dumps(content)
            out.write(encoded)
        
        elif format in ('GeoAMF', 'ArcAMF'):
            import pyamf

            encoded = pyamf.encode(content, 0).read()
            out.write(encoded)

def _sref_4326():
    """
    """
    sref = osr.SpatialReference()
    proj = getProjectionByName('WGS84')
    sref.ImportFromProj4(proj.srs)
    
    return sref

def _reserialize_to_arc(content):
    """ Convert from "geo" (GeoJSON) to ESRI's GeoServices REST serialization.
    
        Much of this cribbed from sample server queries and page 191+ of:
          http://www.esri.com/library/whitepapers/pdfs/geoservices-rest-spec.pdf
    """
    arc_geometry_types = {
        'Point': 'esriGeometryPoint',
        'LineString': 'esriGeometryPolyline',
        'Polygon': 'esriGeometryPolygon',
        'MultiPoint': 'esriGeometryMultipoint',
        'MultiLineString': 'esriGeometryPolyline',
        'MultiPolygon': 'esriGeometryPolygon'
      }
    
    found_geometry_types = set([feat['geometry']['type'] for feat in content['features']])
    found_geometry_types = set([arc_geometry_types.get(type) for type in found_geometry_types])
    
    if len(found_geometry_types) > 1:
        raise KnownUnknown('Arc serialization needs a single geometry type, not ' + ', '.join(found_geometry_types))
    
    response = {'spatialReference': {'wkid': 4326}, 'features': []}
    
    if 'wkid' in content['crs']:
        response['spatialReference'] = {'wkid': content['crs']['wkid']}
    
    elif 'wkt' in content['crs']:
        response['spatialReference'] = {'wkt': content['crs']['wkt']}
    
    for feature in content['features']:
        geometry = feature['geometry']

        if geometry['type'] == 'Point':
            x, y = geometry['coordinates']
            arc_geometry = {'x': x, 'y': y}
        
        elif geometry['type'] == 'LineString':
            path = geometry['coordinates']
            arc_geometry = {'paths': [path]}

        elif geometry['type'] == 'Polygon':
            rings = geometry['coordinates']
            arc_geometry = {'rings': rings}

        elif geometry['type'] == 'MultiPoint':
            points = geometry['coordinates']
            arc_geometry = {'points': points}

        elif geometry['type'] == 'MultiLineString':
            paths = geometry['coordinates']
            arc_geometry = {'paths': paths}

        elif geometry['type'] == 'MultiPolygon':
            rings = reduce(add, geometry['coordinates'])
            arc_geometry = {'rings': rings}

        else:
            raise Exception(geometry['type'])
        
        arc_feature = {'attributes': feature['properties'], 'geometry': arc_geometry}
        response['geometryType'] = arc_geometry_types[geometry['type']]
        response['features'].append(arc_feature)
    
    return response

def _tile_perimeter(coord, projection):
    """ Get a tile's outer edge for a coordinate and a projection.
    
        Returns a list of 17 (x, y) coordinates corresponding to a clockwise
        circumambulation of a tile boundary in a given projection. Projection
        is like those found in TileStache.Geography, used for tile output.
    """
    ul = projection.coordinateProj(coord)
    lr = projection.coordinateProj(coord.right().down())
    
    xmin, ymin, xmax, ymax = ul.x, ul.y, lr.x, lr.y
    xspan, yspan = xmax - xmin, ymax - ymin
    
    perimeter = [
        (xmin, ymin),
        (xmin + 1 * xspan/4, ymin),
        (xmin + 2 * xspan/4, ymin),
        (xmin + 3 * xspan/4, ymin),
        (xmax, ymin),
        (xmax, ymin + 1 * yspan/4),
        (xmax, ymin + 2 * yspan/4),
        (xmax, ymin + 3 * yspan/4),
        (xmax, ymax),
        (xmax - 1 * xspan/4, ymax),
        (xmax - 2 * xspan/4, ymax),
        (xmax - 3 * xspan/4, ymax),
        (xmin, ymax),
        (xmin, ymax - 1 * yspan/4),
        (xmin, ymax - 2 * yspan/4),
        (xmin, ymax - 3 * yspan/4),
        (xmin, ymin)
      ]
    
    return perimeter

def _tile_perimeter_geom(coord, projection):
    """ Get an OGR Geometry object for a coordinate tile polygon.
    
        Uses _tile_perimeter().
    """
    perimeter = _tile_perimeter(coord, projection)
    wkt = 'POLYGON((%s))' % ', '.join(['%.3f %.3f' % xy for xy in perimeter])
    geom = ogr.CreateGeometryFromWkt(wkt)
    
    ref = osr.SpatialReference()
    ref.ImportFromProj4(projection.srs)
    geom.AssignSpatialReference(ref)
    
    return geom

def _feature_properties(feature, layer_definition, whitelist=None):
    """ Returns a dictionary of feature properties for a feature in a layer.
    
        Third argument is an optional list or dictionary of properties to
        whitelist by case-sensitive name - leave it None to include everything.
        A dictionary will cause property names to be re-mapped.
    
        OGR property types:
        OFTInteger (0), OFTIntegerList (1), OFTReal (2), OFTRealList (3),
        OFTString (4), OFTStringList (5), OFTWideString (6), OFTWideStringList (7),
        OFTBinary (8), OFTDate (9), OFTTime (10), OFTDateTime (11).
    """
    properties = {}
    okay_types = ogr.OFTInteger, ogr.OFTReal, ogr.OFTString, ogr.OFTWideString
    
    for index in range(layer_definition.GetFieldCount()):
        field_definition = layer_definition.GetFieldDefn(index)
        field_type = field_definition.GetType()
        
        if field_type not in okay_types:
            try:
                name = [oft for oft in dir(ogr) if oft.startswith('OFT') and getattr(ogr, oft) == field_type][0]
            except IndexError:
                raise KnownUnknown("Found an OGR field type I've never even seen: %d" % field_type)
            else:
                raise KnownUnknown("Found an OGR field type I don't know what to do with: ogr.%s" % name)

        name = field_definition.GetNameRef()
        
        if type(whitelist) in (list, dict) and name not in whitelist:
            continue
        
        property = type(whitelist) is dict and whitelist[name] or name
        properties[property] = feature.GetField(name)
    
    return properties

def _open_layer(driver_name, parameters, dirpath):
    """ Open a layer, return it and its datasource.
    
        Dirpath comes from configuration, and is used to locate files.
    """
    #
    # Set up the driver
    #
    okay_drivers = 'PostgreSQL', 'ESRI Shapefile', 'GeoJSON'
    
    okay_drivers = {'postgis': 'PostgreSQL', 'esri shapefile': 'ESRI Shapefile',
                    'postgresql': 'PostgreSQL', 'shapefile': 'ESRI Shapefile',
                    'geojson': 'GeoJSON'}
    
    if driver_name.lower() not in okay_drivers:
        raise KnownUnknown('Got a driver type Vector doesn\'t understand: "%s". Need one of %s.' % (driver_name, ', '.join(okay_drivers)))

    driver_name = okay_drivers[driver_name.lower()]
    driver = ogr.GetDriverByName(str(driver_name))
    
    #
    # Set up the datasource
    #
    if driver_name == 'PostgreSQL':
        if 'dbname' not in parameters:
            raise KnownUnknown('Need at least a "dbname" parameter for postgis')
    
        conn_parts = []
        
        for part in ('dbname', 'user', 'host', 'password'):
            if part in parameters:
                conn_parts.append("%s='%s'" % (part, parameters[part]))
        
        source_name = 'PG:' + ' '.join(conn_parts)
        
    elif driver_name in ('ESRI Shapefile', 'GeoJSON'):
        if 'file' not in parameters:
            raise KnownUnknown('Need at least a "file" parameter for a shapefile')
    
        file_href = urljoin(dirpath, parameters['file'])
        scheme, h, file_path, q, p, f = urlparse(file_href)
        
        if scheme not in ('file', ''):
            raise KnownUnknown('Shapefiles need to be local, not %s' % file_href)
        
        source_name = file_path

    datasource = driver.Open(str(source_name))

    if datasource is None:
        raise KnownUnknown('Couldn\'t open datasource %s' % source_name)
    
    #
    # Set up the layer
    #
    if driver_name == 'PostgreSQL':
        if 'query' in parameters:
            layer = datasource.ExecuteSQL(str(parameters['query']))
        elif 'table' in parameters:
            layer = datasource.GetLayerByName(str(parameters['table']))
        else:
            raise KnownUnknown('Need at least a "query" or "table" parameter for postgis')

    else:
        layer = datasource.GetLayer(0)

    if layer.GetSpatialRef() is None: 
        raise KnownUnknown('Couldn\'t get a layer from data source %s' % source_name)

    #
    # Return the layer and the datasource.
    #
    # Technically, the datasource is no longer needed
    # but layer segfaults when it falls out of scope.
    #
    return layer, datasource

def _get_features(coord, properties, projection, layer, clipped, projected):
    """ Return a list of features in an OGR layer with properties in GeoJSON form.
    
        Optionally clip features to coordinate bounding box.
    """
    #
    # Prepare output spatial reference - always WGS84.
    #
    if projected:
        output_sref = osr.SpatialReference()
        output_sref.ImportFromProj4(projection.srs)
    else:
        output_sref = _sref_4326()
    
    #
    # Load layer information
    #
    definition = layer.GetLayerDefn()
    layer_sref = layer.GetSpatialRef()
    
    #
    # Spatially filter the layer
    #
    bbox = _tile_perimeter_geom(coord, projection)
    bbox.TransformTo(layer_sref)
    layer.SetSpatialFilter(bbox)
    
    features = []
    
    for feature in layer:
        geometry = feature.geometry().Clone()
        
        if not geometry.Intersect(bbox):
            continue
        
        if clipped:
            geometry = geometry.Intersection(bbox)
        
        if geometry is None:
            # may indicate a TopologyException
            continue
        
        geometry.AssignSpatialReference(layer_sref)
        geometry.TransformTo(output_sref)

        geom = json_loads(geometry.ExportToJson())
        prop = _feature_properties(feature, definition, properties)
        
        features.append({'type': 'Feature', 'properties': prop, 'geometry': geom})
    
    return features

class Provider:
    """ Vector Provider for OGR datasources.
    
        See module documentation for explanation of constructor arguments.
    """
    
    def __init__(self, layer, driver, parameters, clipped, verbose, projected, properties):
        self.layer = layer
        self.driver = driver
        self.clipped = clipped
        self.verbose = verbose
        self.projected = projected
        self.parameters = parameters
        self.properties = properties

    def renderTile(self, width, height, srs, coord):
        """ Render a single tile, return a VectorResponse instance.
        """
        layer, ds = _open_layer(self.driver, self.parameters, self.layer.config.dirpath)
        features = _get_features(coord, self.properties, self.layer.projection, layer, self.clipped, self.projected)
        response = {'type': 'FeatureCollection', 'features': features}
        
        if self.projected:
            sref = osr.SpatialReference()
            sref.ImportFromProj4(self.layer.projection.srs)
            response['crs'] = {'wkt': sref.ExportToWkt()}
            
            if srs == getProjectionByName('spherical mercator').srs:
                response['crs']['wkid'] = 102113
        else:
            response['crs'] = {'srid': 4326, 'wkid': 4326}

        return VectorResponse(response, self.verbose)
        
    def getTypeByExtension(self, extension):
        """ Get mime-type and format by file extension.
        
            This only accepts "geojson" for the time being.
        """
        if extension.lower() == 'geojson':
            return 'text/json', 'GeoJSON'
    
        elif extension.lower() == 'arcjson':
            return 'text/json', 'ArcJSON'
            
        elif extension.lower() == 'geobson':
            return 'application/x-bson', 'GeoBSON'
            
        elif extension.lower() == 'arcbson':
            return 'application/x-bson', 'ArcBSON'
            
        elif extension.lower() == 'geoamf':
            return 'application/x-amf', 'GeoAMF'
            
        elif extension.lower() == 'arcamf':
            return 'application/x-amf', 'ArcAMF'
            
        elif extension.lower() == 'wkt':
            return 'text/x-wkt', 'WKT'

        raise KnownUnknown('Vector Provider only makes .geojson, .arcjson, .geobson, .arcbson, .geoamf, .arcamf and .wkt tiles, not "%s"' % extension)
