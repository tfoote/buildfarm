import os
import logging
import urllib2
import yaml

import apt
import numpy as np

import buildfarm.apt_root
import buildfarm.rosdistro
from rospkg.distro import distro_uri

ros_repos = {'ros': 'http://packages.ros.org/ros/ubuntu/',
             'shadow-fixed': 'http://packages.ros.org/ros-shadow-fixed/ubuntu/',
             'building': 'http://50.28.27.175/repos/building'}

def make_status_page(repo_da_caches, da_strs):
    '''
    Returns the contents of an HTML page showing the current
    build status for all wet and dry packages on all
    supported distributions and architectures.

    :param repo_da_caches: from get_repo_da_caches()
    :param da_strs: list of str from get_da_strs()
    '''
    # Load lists of wet and dry ROS package names
    wet_names_versions = get_wet_names_versions()
    dry_names_versions = get_dry_names_versions()

    # Get the version of each Debian package in each ROS apt repository.
    repo_name_da_to_pkgs = dict(((repo_name, da_str), get_names_versions_from_apt_cache(cache))
                                for repo_name, da_str, cache in repo_da_caches)

    # Make in-memory table showing the latest deb version for each package.
    t = make_versions_table(wet_names_versions, dry_names_versions,
                            repo_name_da_to_pkgs, da_strs,
                            ros_repos.keys())

    # Generate HTML from the in-memory table
    table_html = make_html_from_table(t)
    return make_html_doc(title='Build status page', body=table_html)

def get_repo_da_caches(rootdir, ros_repo_names, da_strs):
    '''
    Returns [(repo_name, da_str, cache), ...]

    For example, get_repo_da_caches('/tmp/ros_apt_caches', ['ros', 'shadow-fixed'], ['quantal_i386'])
    '''
    return [(ros_repo_name, da_str, apt.Cache(rootdir=get_repo_cache_dir_name(rootdir, ros_repo_name, da_str)))
            for ros_repo_name in ros_repo_names
            for da_str in da_strs]

def get_ros_repo_names(ros_repos):
    return ros_repos.keys()

def get_da_strs(distro_arches):
    return [get_dist_arch_str(d, a) for d, a in get_distro_arches()]

def get_distro_arches():
    distros = buildfarm.rosdistro.get_target_distros('groovy')
    arches = ['amd64', 'i386', 'source']
    return [(d, a) for d in distros for a in arches]

def make_versions_table(wet_names_versions, dry_names_versions,
                        repo_name_da_to_pkgs, da_strs, repo_names):
    '''
    Returns an in-memory table with all the information that will be displayed:
    ros package names and versions followed by debian versions for each
    distro/arch.
    '''
    ros_pkgs = get_ros_pkgs_table(wet_names_versions, dry_names_versions)
    left_columns = [('name', object), ('version', object), ('wet', bool),
                    ('ros_repo', object)]
    right_columns = [(da_str, object) for da_str in da_strs]
    columns = left_columns + right_columns
    table = np.empty(len(ros_pkgs)*len(repo_names), dtype=columns)
    repo_da_name_to_deb_version = dict(((repo_name, da_str, p['name']), p['version'])
                                       for (repo_name, da_str), pkgs in repo_name_da_to_pkgs.items()
                                   for p in pkgs)

    for i, (name, version, wet) in enumerate(ros_pkgs):
        for da_str in da_strs:
            for j, repo_name in enumerate(repo_names):
                index = i * len(repo_names) + j
                table['name'][index] = name
                table['version'][index] = version
                table['wet'][index] = wet
                table['ros_repo'][index] = repo_name
                deb_name = buildfarm.rosdistro.debianize_package_name('groovy', name)
                deb_version = repo_da_name_to_deb_version.get((repo_name, da_str, deb_name))
                table[da_str][index] = deb_version

    return table

def get_ros_pkgs_table(wet_names_versions, dry_names_versions):
    return np.array(
        [(name, version, True) for name, version in wet_names_versions] + 
        [(name, version, False) for name, version in dry_names_versions],
        dtype=[('name', object), ('version', object), ('wet', bool)])

def make_html_from_table(table):
    '''
    Makes an HTML table from a numpy array with named columns
    '''
    header = table.dtype.names
    rows = [row for row in table]
    return make_html_table(header, rows)

def get_dist_arch_str(d, a):
    return "%s_%s" % (d, a)

def get_repo_cache_dir_name(rootdir, ros_repo_name, dist_arch):
    return os.path.join(rootdir, ros_repo_name, dist_arch)

def build_repo_caches(rootdir, ros_repos, distro_arches):
    '''
    Builds (or rebuilds) local caches for ROS apt repos.

    For example, build_repo_caches('/tmp/ros_apt_caches', ros_repos,
                                   get_distro_arches())
    '''
    for repo_name, url in ros_repos.items():
        for distro, arch in distro_arches:
            dist_arch = get_dist_arch_str(distro, arch)
            dir = get_repo_cache_dir_name(rootdir, repo_name, dist_arch)
            build_repo_cache(dir, repo_name, url, distro, arch)

def build_repo_cache(dir, ros_repo_name, ros_repo_url, distro, arch):
    logging.info('Setting up an apt directory at %s', dir)
    repo_dict = {ros_repo_name: ros_repo_url}
    buildfarm.apt_root.setup_apt_rootdir(dir, distro, arch,
                                         additional_repos=repo_dict)
    logging.info('Getting a list of packages for %s-%s', distro, arch)
    cache = apt.Cache(rootdir=dir)
    cache.open()
    cache.update()
    # Have to open the cache again after updating.
    cache.open()

def make_html_table_from_names_versions(names_pkgs):
    header = ['package', 'version']
    debify = lambda name: buildfarm.rosdistro.debianize_package_name('groovy', name)
    rows = [(debify(name), d.get('version')) for name, d in names_pkgs]
    rows.sort(key=lambda (pkg, version): pkg)
    return make_html_table(header, rows)

def get_wet_names_versions():
    return get_names_versions(get_wet_names_packages())

def get_dry_names_versions():
    return get_names_versions(get_dry_names_packages())

def get_names_versions(names_pkgs):
    return sorted([(name, d.get('version')) for name, d in names_pkgs],
                  key=lambda (name, version): name)

def get_wet_names_packages():
    '''
    Fetches a yaml file from the web and returns a list of pairs of the form

    [(short_pkg_name, pkg_dict), ...]

    for the wet (catkinized) packages.
    '''
    wet_yaml = get_wet_yaml()
    return wet_yaml['repositories'].items()

def get_wet_yaml():
    url = 'https://raw.github.com/ros/rosdistro/master/releases/groovy.yaml'
    return yaml.load(urllib2.urlopen(url))

def get_dry_names_packages():
    '''
    Fetches a yaml file from the web and returns a list of pairs of the form

    [(short_pkg_name, pkg_dict), ...]

    for the dry (rosbuild) packages.
    '''
    dry_yaml = get_dry_yaml()
    return [(name, d) for name, d in dry_yaml['stacks'].items() if name != '_rules']

def get_dry_yaml():
    return yaml.load(urllib2.urlopen(distro_uri('groovy')))

def make_html_doc(title, body):
    '''
    Returns the contents of an HTML page, given a title and body.
    '''
    return '''\
<html>
\t<head>
\t\t<title>%(title)s</title>
\t</head>
\t<body>
%(body)s
\t</body>
</html>
''' % locals()

def make_html_table(header, rows):
    '''
    Returns a string containing an HTML-formatted table, given a header and some
    rows.

    >>> make_html_table(header=['a'], rows=[[1], [2]])
    '<table>\\n\\t<tr><th>a</th></tr>\\n\\t<tr><td>1</td></tr>\\n\\t<tr><td>2</td></tr>\\n</table>\\n'

    '''
    header_str = '\t<tr>' + ''.join('<th>%s</th>' % c for c in header) + '</tr>'
    rows_str = '\n'.join('\t<tr>' + ''.join('<td>%s</td>' % c for c in r) + '</tr>' 
                         for r in rows)
    return '''\
<table>
%s
%s
</table>
''' % (header_str, rows_str)

def get_names_versions_from_apt_cache(cache):
    return [{'name': k, 'version': cache[k].candidate.version} for k in cache.keys()
            if 'ros-groovy' in k]

def main():
    import argparse
    import BaseHTTPServer

    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

    # Parse command line args
    p = argparse.ArgumentParser(description='Start web server for deb build status')
    rd_help = '''\
Root directory containing ROS apt caches.
This should be created using status_page.build_repo_caches().
'''
    p.add_argument('rootdir', help=rd_help)
    args = p.parse_args()

    class Handler(BaseHTTPServer.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            da_strs = get_da_strs(get_distro_arches())
            ros_repo_names = get_ros_repo_names(ros_repos)
            repo_da_caches = get_repo_da_caches(args.rootdir, ros_repo_names, da_strs)
            page = make_status_page(repo_da_caches, da_strs)
            self.wfile.write(page)

    daemon = BaseHTTPServer.HTTPServer(('', 8080), Handler)
    while True:
        daemon.handle_request()

if __name__ == '__main__':
    main()
