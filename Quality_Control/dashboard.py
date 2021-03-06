import os
import glob
import json
import subprocess
import sys
import importlib


from nipype.utils.filemanip import (load_json, save_json, split_filename, fname_presuffix, copyfile)
from nipype.utils.filemanip import loadcrash
from xml.etree.ElementTree import Element, SubElement, Comment, tostring
from xml.dom import minidom
import h5py
import minc2volume_viewer as minc2volume
import distutils
from distutils import dir_util

import nipype.interfaces.minc as minc
import nipype.pipeline.engine as pe
import nipype.interfaces.io as nio
import nipype.interfaces.utility as util
import nipype.interfaces.utility as niu
from nipype.interfaces.utility import Rename

from Extra.conversion import  nii2mncCommand

from Masking import masking as masking
import Registration.registration as reg
import Initialization.initialization as init
import Partial_Volume_Correction.pvc as pvc 
import Results_Report.results as results
import Tracer_Kinetic.tka as tka
from Tracer_Kinetic import reference_methods, ecat_methods
import Quality_Control.qc as qc
import Test.test_group_qc as tqc
from Masking import surf_masking
global path
path = os.path.dirname(os.path.abspath(__file__))
path_split = path.split(os.sep)
pvc_path = '/'.join(path_split[0:-1])+os.sep+"Partial_Volume_Correction"+os.sep+"methods"
tka_path = '/'.join(path_split[0:-1])+os.sep+"Tracer_Kinetic"+os.sep+"methods"
sys.path.insert(0, pvc_path)
sys.path.insert(0, tka_path)
importlib.import_module("pvc_method_GTM")
importlib.import_module("quant_method_lp")

def cmd(command):
    return subprocess.check_output(command.split(), universal_newlines=True).strip()

def adjust_hdr(mincfile):
    f = h5py.File(mincfile,'r')
    n_dims = len(f['minc-2.0/dimensions'])
    # list_dims = ['xspace', 'yspace', 'zspace', 'time']
    # list_dims.pop() if ndims == 3 else False
    list_dims = ['xspace', 'yspace', 'zspace']  
    for dim in list_dims:
        dir_cosine = {
            "xspace" : '1.,0.,0.',
            "yspace" : '0.,1.,0.',
            "zspace" : '0.,0.,1.',
        } [str(dim)]
        cmd("minc_modify_header -sinsert {}:direction_cosines='{}' {}".format(dim, dir_cosine, mincfile))
    if n_dims == 4:
        cmd("minc_modify_header -dinsert time:start=0 {}".format(mincfile))
        cmd("minc_modify_header -dinsert time:step=1 {}".format(mincfile))

def mnc2vol(mincfile):
    f = h5py.File(mincfile)
    datatype = str(f['minc-2.0/image/0']['image'].dtype)
    rawfile = mincfile+'.raw'
    headerfile = mincfile+'.header'
    minc2volume.make_raw(mincfile, datatype, rawfile)
    minc2volume.make_header(mincfile, datatype, headerfile)


def prettify(elem):
    """Return a pretty-printed XML string for the Element.
    """
    rough_string = tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ", encoding='UTF-8')


def generate_xml_nodes(opts, arg):

    listOfNodes = [
            {"name" : "pet2mri", 
             "mnc_inputs" : {"node" : "pet2mri", "file" : 'in_target_file'},
             "mnc_outputs" : {"node" : "pet2mri", "file" : 'out_file_img'}
            },
            {"name" : "pvc",
             "mnc_inputs" : {"node" : opts.pvc_method, "file" : 'in_file'},
             "mnc_outputs" : {"node" : opts.pvc_method, "file" : 'out_file'}
            },
            {"name" : "tka",
             "mnc_inputs" : {"node" : "convertParametric", "file" : 'output_file'},
             "mnc_outputs" : {"node" : "pet2mri", "file" : 'in_target_file'}
            }
            ]

    filename=opts.targetDir+"/preproc/graph1.json";
    fp = file(filename, 'r')
    data=json.load(fp)
    fp.close()

    xmlQC = Element('qc')
    listVolumes = list();

    for subjIdx in range(0,len(data["groups"])):
        for nodeID in range(data["groups"][subjIdx]["procs"][0],data["groups"][subjIdx]["procs"][-1]):
            nodeName = "_".join(data["nodes"][nodeID]["name"].split("_")[1:])
            if nodeName == "datasource":
                nodeReport = loadcrash(opts.targetDir+"/preproc/"+data["nodes"][nodeID]["result"])
                for key, value in nodeReport.inputs.items():
                    if key == "acq":
                        acq = str(value)
                    if key == "cid":
                        cid = str(value)
                    if key == "sid":
                        sid = str(value)
                    if key == "task":
                        task = str(value)
                    if key == "rec":
                        rec = str(value)
                xmlscan = SubElement(xmlQC, 'scan')
                xmlscan.set('acq', acq)
                xmlscan.set('sid', sid)
                xmlscan.set('cid', cid)
                xmlscan.set('task', task)
                xmlscan.set('rec', rec)

        for x in listOfNodes :
            xmlnode = SubElement(xmlscan, 'node')
            xmlnode.set('name', x['name'])
            for nodeID in range(data["groups"][subjIdx]["procs"][0],data["groups"][subjIdx]["procs"][-1]):
                nodeName = "_".join(data["nodes"][nodeID]["name"].split("_")[1:])
                if nodeName == x["mnc_inputs"]["node"]:
                    nodeReport = loadcrash(opts.targetDir+"/preproc/"+data["nodes"][nodeID]["result"])
                    xmlmnc = SubElement(xmlnode, 'inMnc')
                    for key, value in nodeReport.inputs.items():
                        if key in x['mnc_inputs']["file"]:
                            value = value[0] if type(value) == list else value
                            xmlkey = SubElement(xmlmnc, str(key))
                            xmlkey.text = str(value).replace(opts.sourceDir+"/",'').replace(opts.targetDir+"/",'')
                            listVolumes.append(str(value))

                if nodeName == x["mnc_outputs"]["node"]:
                    nodeReport = loadcrash(opts.targetDir+"/preproc/"+data["nodes"][nodeID]["result"])
                    xmlmnc = SubElement(xmlnode, 'outMnc')
                    for key, value in nodeReport.inputs.items():
                        if key in x['mnc_outputs']["file"]:
                            value = value[0] if type(value) == list else value
                            xmlkey = SubElement(xmlmnc, str(key))
                            xmlkey.text = str(value).replace(opts.sourceDir+"/",'').replace(opts.targetDir+"/",'')
                            listVolumes.append(str(value))                        


    with open(opts.targetDir+"/preproc/dashboard/public/nodes.xml","w") as f:
        f.write(prettify(xmlQC))

    for mincfile in listVolumes:
        rawfile = mincfile+'.raw'
        headerfile = mincfile+'.header'
        if not os.path.exists(rawfile) or not os.path.exists(headerfile):
            adjust_hdr(mincfile)
            mnc2vol(mincfile)


def generate_dashboard(opts, arg):
    if not os.path.exists(opts.targetDir+"/preproc/dashboard/") :
        os.makedirs(opts.targetDir+"/preproc/dashboard/");
    distutils.dir_util.copy_tree(path+'/dashboard_web', opts.targetDir+'/preproc/dashboard', update=1, verbose=0)
    generate_xml_nodes(opts, arg);
    os.chdir(opts.targetDir+'/preproc/dashboard/public/')
    if os.path.exists(os.path.join(opts.targetDir,'preproc/dashboard/public/preproc')):
        os.remove(os.path.join(opts.targetDir,'preproc/dashboard/public/preproc'))
    os.symlink('../../../preproc', os.path.join(opts.targetDir,'preproc/dashboard/public/preproc'))
    for sub in glob.glob(os.path.join(opts.sourceDir,'sub*')):
        if os.path.isdir(sub):
            dest = os.path.join(opts.targetDir,'preproc/dashboard/public/',os.path.basename(sub))
            if os.path.exists(dest):
                os.remove(dest)
            os.symlink(sub, dest)


def link_stats(opts, arg):
    if not os.path.exists(opts.targetDir+"/preproc/dashboard/") :
        os.makedirs(opts.targetDir+"/preproc/dashboard/");
    # distutils.dir_util.copy_tree('/opt/appian/APPIAN/Quality_Control/dashboard_web', opts.targetDir+'/preproc/dashboard', update=1, verbose=0)
    if os.path.exists(os.path.join(opts.targetDir,'preproc/dashboard/public/stats')):
        os.remove(os.path.join(opts.targetDir,'preproc/dashboard/public/stats'))
    os.symlink('../../stats', os.path.join(opts.targetDir,'preproc/dashboard/public/stats'))
