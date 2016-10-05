# Copyright (C) 2013, Carlo de Franchis <carlodef@gmail.com>
# Copyright (C) 2013, Gabriele Facciolo <gfacciol@gmail.com>

# This Python file uses the following encoding: utf-8
from __future__ import print_function
import numpy as np
import os
import sys
import subprocess
import tempfile
import urlparse
import urllib2
import re


# add the current folder to system path
current_dir = os.path.dirname(os.path.abspath(__file__))
os.environ['PATH'] = current_dir + os.pathsep + os.environ['PATH']

# global variable
# list of intermediary files generated by the script
garbage = list()

def tmpfile(ext=''):
    """
    Creates a temporary file in the /tmp directory.

    Args:
        ext: desired file extension

    Returns:
        absolute path to the created file

    The path of the created file is added to the garbage list to allow cleaning
    at the end of the pipeline.
    """
    fd, out = tempfile.mkstemp(suffix = ext, prefix = 's2p_', dir = '.')
    garbage.append(out)
    os.close(fd)           # http://www.logilab.org/blogentry/17873
    return out


def run(cmd):
    """
    Runs a shell command, and print it before running.

    Arguments:
        cmd: string to be passed to a shell

    Both stdout and stderr of the shell in which the command is run are those
    of the parent process.
    """
    print(cmd)
    subprocess.call(cmd, shell=True, stdout=sys.stdout, stderr=subprocess.STDOUT,
        env=os.environ)
    return


def shellquote(s):
    return "'" + s.replace("'", "'\\''") + "'"



def image_size_gdal(im):
    """
    Reads the width and height of an image, using gdal.

    Args:
        im: path to the input image file
    Returns:
        a tuple of size 2, giving width and height
    """
    try:
        with open(im):
            p1 = subprocess.Popen(['gdalinfo', im], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(['grep', 'Size'], stdin=p1.stdout, stdout=subprocess.PIPE)
            line = p2.stdout.readline()
            out = re.findall(r"[\w']+", line)
            nc = int(out[2])
            nr = int(out[3])
            return (nc, nr)
    except IOError:
        print("image_size_gdal: the input file %s doesn't exist" % str(im))
        sys.exit()


def image_size_tiffinfo(im):
    """
    Reads the width and height of an image, using tiffinfo.

    Args:
        im: path to the input tif image file
    Returns:
        a tuple of size 2, giving width and height
    """
    if not im.lower().endswith('.tif'):
        print("image_size_tiffinfo function works only with TIF files")
        print("use image_size_gdal or image_size instead")
        sys.exit()
    try:
        with open(im):
            # redirect stderr to /dev/null on tiffinfo call to discard noisy
            # msg about unknown field with tag 42112
            fnull = open(os.devnull, "w")
            p1 = subprocess.Popen(['tiffinfo', im], stdout=subprocess.PIPE,
                    stderr=fnull)
            p2 = subprocess.Popen(['grep', 'Image Width'], stdin=p1.stdout,
                    stdout=subprocess.PIPE)
            line = p2.stdout.readline()
            out = re.findall(r"[\w']+", line)
            nc = int(out[2])
            nr = int(out[5])
            return (nc, nr)
    except IOError:
        print("image_size_tiffinfo: the input file %s doesn't exist" % str(im))
        sys.exit()



def bounding_box2D(pts):
    """
    bounding box for the points pts
    """
    dim = len(pts[0])      #should be 2
    bb_min = [ min([ t[i] for t in pts ]) for i in range(0, dim) ]
    bb_max = [ max([ t[i] for t in pts ]) for i in range(0, dim) ]
    x, y, w, h = bb_min[0], bb_min[1], bb_max[0]-bb_min[0], bb_max[1]-bb_min[1]
    return x, y, w, h


def image_crop_TIFF(im, x, y, w, h, out=None):
    """
    Crops tif images.

    Args:
        im: path to a tif image, or to a tile map file (*.til)
        x, y, w, h: four integers definig the rectangular crop in the image.
            (x, y) is the top-left corner, and (w, h) are the dimensions of the
            rectangle.
        out (optional): path to the output crop

    Returns:
        path to cropped tif image

    The crop is made with the gdal_translate binary, from gdal library. We
    tried to use tiffcrop but it fails.
    """
    if (int(x) != x or int(y) != y):
        print('Warning: image_crop_TIFF will round the coordinates of your crop')

    if out is None:
        out = tmpfile('.tif')

    try:
        with open(im, 'r'):
            # do the crop with gdal_translate, with option to remove any GDAL or GeoTIFF tag
            run('gdal_translate -co profile=baseline -srcwin %d %d %d %d %s %s' % (x,
                y, w, h, shellquote(im), shellquote(out)))

    except IOError:
        print("""image_crop_TIFF: input image not found! Verify your paths to
                 Pleiades full images""")
        sys.exit()

    return out


def run_binary_on_list_of_points(points, binary, option=None, binary_workdir=None):
    """
    Runs a binary that reads its input on stdin.

    Args:
        points: numpy array containing all the input points, one per line
        binary: path to the binary. It is supposed to write one output value on
            stdout for each input point
        option: optional option to pass to the binary
        binary_workdir: optional workdir for the binary to be launched

    Returns:
        a numpy array containing all the output points, one per line.
    """
    # run the binary
    pts_file = tmpfile('.txt')
    np.savetxt(pts_file, points, '%.18f')
    p1 = subprocess.Popen(['cat', pts_file], stdout = subprocess.PIPE)
    if binary_workdir == None:
        binary_workdir = os.getcwd()
    if option:
        p2 = subprocess.Popen([binary, option], stdin = p1.stdout, stdout =
            subprocess.PIPE, cwd = binary_workdir)
    else:
        p2 = subprocess.Popen([binary], stdin = p1.stdout, stdout =
            subprocess.PIPE, cwd = binary_workdir)

    # recover output values: first point first, then loop over all the others
    line = p2.stdout.readline()
    out = np.array([[float(val) for val in line.split()]])
    for i in range(1, len(points)):
        line = p2.stdout.readline()
        l = [float(val) for val in line.split()]
        out = np.vstack((out, l))

    return out


def image_zoom_gdal(im, f, out=None, w=None, h=None):
    """
    Zooms an image using gdal (average interpolation)

    Args:
        im: path to the input image
        f:  zoom factor. f in [0,1] for zoom in, f in [1 +inf] for zoom out.
        out (optional): path to the ouput file
        w, h (optional): input image dimensions

    Returns:
        path to the output image. In case f=1, the input image is returned
    """
    if f == 1:
        return im

    if out is None:
        out = tmpfile('.tif')

    tmp = tmpfile('.tif')

    if w is None or h is None:
        sz = image_size_tiffinfo(im)
        w = sz[0]
        h = sz[1]

    # First, we need to make sure the dataset has a proper origin/spacing
    run('gdal_translate -a_ullr 0 0 %d %d %s %s' % (w/float(f), -h/float(f), im, tmp))

    # do the zoom with gdalwarp
    run('gdalwarp -ts %d %d %s %s' %  (w/float(f), h/float(f), tmp, out))
    return out


def url_with_authorization_header(from_url):
    """
    Add authorization header

    Args:
        from_url: url of the file to download
    """
    scheme, netloc, path, param, query = urlparse.urlsplit(from_url)
    if "@" in netloc:
        userinfo = netloc.rsplit("@",1)[0]
        if ":" in userinfo:
            username = userinfo.rsplit(":",1)[0]
            password = userinfo.rsplit(":",1)[1]
            netloc = netloc.rsplit("@",1)[1]

            from_url = urlparse.urlunsplit((scheme, netloc, path, param, query))

            if username != None and password != None:
                request = urllib2.Request(from_url)
                base64string = base64.encodestring('%s:%s' % (username, password)).replace('\n', '')
                request.add_header("Authorization", "Basic %s" % base64string)
                from_url = request

    return from_url


def download(to_file, from_url):
    """
    Download a file from the internet.

    Args:
        to_file: path where to store the downloaded file
        from_url: url of the file to download
    """
    f = open(to_file, 'wb')
    file_size_dl = 0
    block_sz = 8192

    try:
        u = urllib2.urlopen(from_url)
        meta = u.info()
        file_size = int(meta.getheaders("Content-Length")[0])
        print("Downloading: %s Bytes: %s" % (to_file, file_size))

        while True:
            buffer = u.read(block_sz)
            if not buffer:
                break

            file_size_dl += len(buffer)
            f.write(buffer)
            status = r"%10d  [%3.2f%%]" % (file_size_dl, file_size_dl * 100. / file_size)
            status = status + chr(8)*(len(status)+1)
            print(status, end=" ")

    except urllib2.URLError as e:
        print("Download failed: ", e)

    f.close()


