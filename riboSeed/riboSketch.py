#!/usr/bin/env python3
#-*- coding: utf-8 -*-

import numpy as np
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch

from Bio import SeqIO
from Bio.SeqFeature import SeqFeature, FeatureLocation
import os
import sys
import argparse
import glob
import subprocess

mycolors = {
    "pinkish": mpl.colors.ColorConverter().to_rgba(
        "#ff4c05", alpha=1),
    "redish": mpl.colors.ColorConverter().to_rgba(
        "#ff4c05", alpha=1),
    "yellish": mpl.colors.ColorConverter().to_rgba(
        "#FFFB07", alpha=1),
    "greenish": mpl.colors.ColorConverter().to_rgba(
        "#04FF08", alpha=1),
    "bluish": mpl.colors.ColorConverter().to_rgba(
        "#06B9FF", alpha=1),
    "greyish": mpl.colors.ColorConverter().to_rgba(
        "#7E7F97", alpha=1),
    "clear": mpl.colors.ColorConverter().to_rgba(
        "#FF012F", alpha=0),
}


bgcols = {
    "purle": mpl.colors.ColorConverter().to_rgba(
        "#EB87A3", alpha=0.5),
    "green": mpl.colors.ColorConverter().to_rgba(
        "#5EA662", alpha=0.5),
    "yellow": mpl.colors.ColorConverter().to_rgba(
        "#EBE418", alpha=0.5),
    "red": mpl.colors.ColorConverter().to_rgba(
        "#EB7D7D", alpha=0.5),
    "blue": mpl.colors.ColorConverter().to_rgba(
        "#6795A6", alpha=0.5),
    }


def get_args():  # pragma: no cover
    """get the arguments as a main parser with subparsers
    for named required arguments and optional arguments
    """
    parser = argparse.ArgumentParser(
        description="Pretty up the plots generated by mauve contig mover",
        add_help=False)
    parser.add_argument("indir",
                        help="dir containing a genbank file and other file")
    requiredNamed = parser.add_argument_group('required named arguments')
    requiredNamed.add_argument("-o", "--outdir",
                               help="output directory; default: %(default)s",
                               default=os.getcwd(),
                               type=str, dest="outdir")

    optional = parser.add_argument_group('optional arguments')
    optional.add_argument("-f", "--assembly_ext", dest="assembly_ext",
                          help="extenssion of reference, usually fasta",
                          default="fasta", type=str)
    optional.add_argument("-g", "--ref_ext", dest="ref_ext",
                          help="extension of reference, usually .gb",
                          default="gb", type=str)
    optional.add_argument("-n", "--names",
                          help="name the resulting plot and output " +
                          "dirs; comma-separate",
                          default=None, dest="names",
                          action="store", type=str)
    optional.add_argument("-r", "--replot",
                          help="replot, using a previous run of analyses",
                          default=False, dest="replot",
                          action="store_true")
    optional.add_argument("--mauve_exe", dest="mauve_exe",
                          action="store", default="~/mauve_snapshot_2015-02-13/Mauve.jar",
                          help="path to Mauve.jar; " +
                          "default: %(default)s")
    # had to make this explicitly to call it a faux optional arg
    optional.add_argument("-h", "--help",
                          action="help", default=argparse.SUPPRESS,
                          help="Displays this help message")
    args = parser.parse_args()
    return args


def parseDirContents(dirname, ref_ext, assembly_ext):
    """retursn a tuple (ref, [assembly1, assembly2, etc])
    """
    return (glob.glob(dirname + "*" + ref_ext)[0],
            glob.glob(dirname + "*" + assembly_ext))


def makeContigMovercmds(ref, files, outdir, mauve_exe):
    cmds = []
    results = []
    for f in files:
        thisdir = os.path.join(outdir, "ref_vs_" +
                               os.path.splitext(os.path.basename(f))[0])
        cmd = "java -Xmx500m -cp {0} org.gel.mauve.contigs.ContigOrderer -output {1} -ref {2} -draft {3}".format(
            mauve_exe,
            thisdir,
            ref,
            f)
        cmds.append(cmd)
        results.append(thisdir)
    return(cmds, results)


def findBestAlignments(outdir):
    dirs = os.listdir(outdir)
    print(dirs)
    maxiter  = max([int(x.split("alignment")[1]) for x in dirs])
    print(maxiter)
    maxiterdir = [x for x in dirs if int(x.split("alignment")[1]) == maxiter]
    print(maxiterdir)
    return(os.path.join(outdir, maxiterdir[0], ""))


def parseBackbones(filelist):
    """ Given a list of .backbones files, write out as nested list
    """
    comps_list = []
    for i, f in enumerate(filelist):
        with open(f, "r") as infile:
            temp = [x.strip().split("\t") for x in infile.readlines()]
            temp2 = []
            for sublist in temp[1:len(temp)]:
                temp2.append([int(x) for x in sublist])
                # temp = [int(x) for x in [y for y in temp[1:len(temp)]]]
        comps_list.append(temp2)  # get rid of header
    return (comps_list)


def plot_mauve_compare(refgb,
                       assembly_list,
                       backbones_list,
                       bufferlen=10000,
                       breakwidth=40,
                       aspect=.6,
                       names=["Position", "Entropy"],
                       title="Shannon Entropy by Position",
                       output_prefix="entropy_plot.png"):
    assert len(assembly_list) == len(backbones_list), \
        "must have same amount of assemblies as backbones"
    with open(refgb, "r") as rg:
        ref_recs = list(SeqIO.parse(rg, "genbank"))
    assembly_lens = [[sum([len(x) for x in ref_recs])]]
    for seq in assembly_list:
        with open(seq, "r") as inseq:
            assembly_lens.append([len(x) for x in
                                  list(SeqIO.parse(inseq, "fasta"))])
    backbones = parseBackbones(backbones_list)
    npanels = len(assembly_list) + 1
    max_combined_len = max([sum(x) for x in assembly_lens]) + bufferlen
    print(max_combined_len)
    fig, ax = plt.subplots(1, 1)
    ax.set_title(title, y=1.08)
    relheight = max_combined_len * aspect
    coding_height = .05 * relheight
    # set the centers as starting relative to  relheight - (2* codingdepth)
    relinner = relheight - (coding_height * 3)
    centers = []
    for i in range(npanels):
        if i == 0:
            centers.append(relheight - (coding_height * 1.5))
        elif i == npanels - 1:
            centers.append(0 + (coding_height * 1.5))
        else:
            centers.append(relheight - ((coding_height * 1.5) +
                                        (relinner / float(npanels - 1))  * i))
    xmin, xmax = 0, max_combined_len
    ymin, ymax = 0, relheight
    ax.set_xlim([xmin, xmax])
    ax.set_ylim([ymin, ymax])
    #  plot the color shadings
    unused_cols = ["red", "green", "yellow", "purple", "red", "blue"]
    nudge = coding_height / 2
    patch_list = []
    for i, bblist in enumerate(backbones):
        for As, Ae, Bs, Be in bblist:
            if (Bs == 0 and Be == 0) or \
               (As == 0 and Ae == 0):
                continue
            verts = [
                (Bs, centers[i + 1] + nudge),  # left, bottom
                (As, centers[0] - nudge),  # left, top
                (Ae, centers[0] - nudge),  # right, top
                (Be, centers[i + 1] + nudge),  # right, bottom
                (Bs, centers[i + 1] + nudge),  # ignored
            ]

            codes = [mpl.path.Path.MOVETO,
                     mpl.path.Path.LINETO,
                     mpl.path.Path.LINETO,
                     mpl.path.Path.LINETO,
                     mpl.path.Path.CLOSEPOLY]

            path = mpl.path.Path(verts, codes)

            patch = patches.PathPatch(path,
                                      facecolor=bgcols.get(unused_cols[0]),
                                      edgecolor=mycolors.get("clear"),
                                      lw=2)
            patch_list.append(patch)
        unused_cols.pop(0)
    # we want the first annotation on top
    [ax.add_patch(p) for p in list(reversed(patch_list))]

    # add annotations
    last_chrom_end = 0
    for record in ref_recs:
        # coding sequence
        print(centers[0] * .005)
        coding_box = FancyBboxPatch(
            (last_chrom_end, centers[0] - coding_height / 2),
            len(record), coding_height,
            boxstyle="round,pad=0,rounding_size=" + str(centers[0] / 50),
            mutation_aspect=.5,
            # mutation_scale=.5,
            fc=mycolors['greyish'],
            ec=mycolors['clear']
        )
        # buffer_box = FancyBboxPatch(
        #     (last_chrom_end + len(record), centers[0] - coding_height / 2),
        #     last_chrom_end + len(record) + bufferlen, coding_height,
        #     boxstyle="round,pad=0,rounding_size=0",
        #     mutation_aspect=.5,
        #     # mutation_scale=.5,
        #     fc=mycolors['clear'],
        #     ec=mycolors['clear']
        # )
        last_chrom_end = last_chrom_end + len(record)
        ax.add_patch(coding_box)
        # ax.add_patch(buffer_box)
        for i, feature in enumerate(record.features):
            if feature.type != "rRNA" and i == 0:
                #Exclude this feature
                continue
            feat_len = \
                feature.location.end.position - feature.location.start.position
            anno_box = FancyBboxPatch(
                (feature.location.start.position,
                 centers[0] - coding_height),
                feat_len, coding_height * 2,
                boxstyle="round,pad=0,rounding_size=" + str(feat_len / 2),
                mutation_aspect=.5,
                # mutation_scale=.5,
                fc=mycolors['redish'],
                ec=mycolors['redish']
            )

            ax.add_patch(anno_box)

    for i in range(npanels):
    # for each assembly
        if i == 0:
            continue
        with open(assembly_list[i - 1], "r") as infile:
            contigs = list(SeqIO.parse(infile, "fasta"))
        last_contig_end = 0
        for record in contigs:

            coding_box = FancyBboxPatch(
                (last_contig_end, centers[i] - coding_height / 2),
                len(record), coding_height,
                boxstyle="round,pad=0,rounding_size=" + str(centers[i] / 50),
                mutation_aspect=.5,
                # mutation_scale=.5,
                fc=mycolors['greyish'],
                ec=mycolors['clear']
            )
            buffer_box = FancyBboxPatch(
                (last_contig_end + len(record) - breakwidth, centers[i] - coding_height),
                breakwidth, coding_height * 2,
                boxstyle="round,pad=0,rounding_size=0",
                mutation_aspect=.5,
                # mutation_scale=.5,
                fc="black",
                ec=mycolors['clear']
            )
            last_contig_end = last_contig_end + len(record)
            ax.add_patch(coding_box)
            ax.add_patch(buffer_box)

    ax.set_yticks(np.array(centers))
    ax.set_yticklabels(names)
    ax.get_yaxis().set_label_coords(-.05, .1)
    ax.yaxis.set_ticks_position('left')
    ax.xaxis.set_ticks_position('top')
    # ax.tick_params(axis='y', colors='dimgrey')
    ax.tick_params(axis='x', colors='dimgrey')
    ax.yaxis.label.set_color('black')
    ax.xaxis.label.set_color('black')
    ax.spines['top'].set_visible(True)
    ax.spines["left"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_visible(False)

    plt.tight_layout()
    fig.subplots_adjust(hspace=0)
    fig.set_size_inches(12, 12 * aspect)
    fig.savefig(str(output_prefix + '.png'), dpi=(200))
    fig.savefig(str(output_prefix + '.pdf'), dpi=(200))
    return 0

if __name__ == "__main__":
    args = get_args()
    try:
        os.makedirs(args.outdir)
        os.makedirs(os.path.join(args.outdir, "reordering"))
    except:
        if args.replot:
            print("using existing output dir and alignment results")
        else:
            sys.stderr.write("Output Directory already exists!\n")
            sys.exit(1)
    if not os.path.isdir(os.path.join(args.indir, "")) or len(
            os.listdir(os.path.join(args.indir, ""))) == 0:
        print("input directory doesnt exist or is empty! Exiting...")
        sys.exit(1)
    gb, fastas = parseDirContents(dirname=os.path.join(args.indir, ""),
                                  ref_ext=args.ref_ext,
                                  assembly_ext=args.assembly_ext)

    cmds, result_paths = makeContigMovercmds(
        ref=gb, files=fastas,
        outdir=os.path.join(args.outdir, "reordering"),
        mauve_exe=args.mauve_exe)
    if not args.replot:
        for i in cmds:
            try:
                print(i)
                subprocess.run([i],
                               shell=sys.platform != "win32",
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               check=True)
            except Exception as e:
                print(e)
                sys.exit(1)
    # get the path to the dir for the last iteration of the reorderer
    best_aln_dirs = [findBestAlignments(i) for i in result_paths]
    assembly_list = []
    backbone_list = []
    for d in best_aln_dirs:
        assembly_list.append(glob.glob(d + "*.fasta")[0])
        backbone_list.append(glob.glob(d + "*.backbone")[0])
    if args.names is None:
        names = [os.path.splitext(os.path.basename(x))[0] for
                 x in [gb] + fastas]
    else:
        names = args.names.split(",")

    plot_mauve_compare(refgb=gb,
                       assembly_list=assembly_list,
                       backbones_list=backbone_list,
                       names=names,
                       bufferlen=1000,
                       breakwidth=100,
                       title="",
                       aspect=.4,
                       output_prefix=os.path.join(args.outdir,
                                                  "PrettyMauve"))