#!/usr/bin/env python3
#-*- coding: utf-8 -*-

"""
Minor Version Revisions:
 - no more individual versions; all treated as pipeline from here on
starting at version 0.0.940
Created on Sun Jul 24 19:33:37 2016

See README.md for more info and usage

###



"""
import argparse
import sys
import time
import re
import logging
import os
import shutil
import multiprocessing
import subprocess

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.Alphabet import IUPAC
# need this line for unittesting
sys.path.append(os.path.join('..', 'riboSeed'))
# print(sys.path)
from pyutilsnrw.utils3_5 import set_up_logging, make_outdir, \
    combine_contigs, \
    copy_file, check_installed_tools, get_ave_read_len_from_fastq, \
    get_number_mapped, extract_mapped_and_mappedmates, clean_temp_dir, \
    output_from_subprocess_exists, keep_only_first_contig, get_fasta_lengths, \
    file_len, check_version_from_init, check_version_from_cmd

from riboSnag import parse_clustered_loci_file, \
    extract_coords_from_locus, \
    stitch_together_target_regions, get_genbank_rec_from_multigb, \
    pad_genbank_sequence, prepare_prank_cmd, prepare_mafft_cmd, \
    calc_Shannon_entropy, calc_entropy_msa, \
    annotate_msa_conensus, plot_scatter_with_anno, \
    profile_kmer_occurances, plot_pairwise_least_squares, make_msa, \
    LociCluster, Locus


## GLOBALS
SAMTOOLS_MIN_VERSION = '1.3.1'
PACKAGE_VERSION = '0.4.0'
#################################### classes ###############################


class SeedGenome(object):
    """ organizes the clustering process instead of dealing with individual
    seeds alone
    This holds all he data pertaining to te clustering of a scaffold
    """
    def __init__(self, genbank_path, final_contigs_dir=None,
                 this_iteration=0, ref_fasta=None, next_reference_path=None,
                 loci_clusters=None, output_root=None, initial_map_bam=None,
                 unmapped_ngsLib=None, name=None, iter_mapping_list=None,
                 reads_mapped_txt=None, unmapped_mapping_list=None, max_iterations=None,
                 initial_map_sam=None, unmapped_sam=None,  # initial_mapping_ob=None,
                 clustered_loci_txt=None, seq_records=None, initial_map_prefix=None,
                 initial_map_sorted_bam=None, master_ngs_ob=None,
                 assembled_seeds=None, logger=None):
        self.name = name  # get from commsanline in case running multiple
        self.this_iteration = this_iteration  # this should always start at 0
        self.max_iterations = max_iterations  # this should always start at 0
        self.iter_mapping_list = iter_mapping_list  # this should always start at 0
        # The main output for resulting files, all other are relative
        self.output_root = output_root
        # from command line
        self.genbank_path = genbank_path
        # This is created from genbank
        self.ref_fasta = ref_fasta  # this is made dynamically
        # output from riboSelect
        self.clustered_loci_txt = clustered_loci_txt
        # holds a list of LociCluster objects
        self.loci_clusters = loci_clusters
        # this set below with attach_genome_seqRecor
        self.seq_records = seq_records  # this is set
        # self.initial_mapping_ob = initial_mapping_ob
        # this will hold prefix for initial mapping
        self.initial_map_prefix = initial_map_prefix  # set this dynamically
        # extracting reads by position requires an indexed, sorted bam
        self.initial_map_sorted_bam = initial_map_sorted_bam  # set dynamically
        # inial mapping result (combined s and pe)
        self.initial_map_bam = initial_map_bam  # set this dynamically
        # see above
        self.initial_map_sam = initial_map_sam  # set this dynamically
        # holds user-provided sequencing data. Keep intact for final assembly
        self.master_ngs_ob = master_ngs_ob  # for ngslib object
        # each round of seeding results in a ml list for remaining reads
        self.unmapped_mapping_list = unmapped_mapping_list
        # holds dynamically updated list of remaining unmapped
        self.unmapped_sam = unmapped_sam
        # after paritioning, the last mapping list is extraced into this ngs ob
        self.unmapped_ngsLib = unmapped_ngsLib
        # path to file mapped readnames are appended to
        self.reads_mapped_txt = reads_mapped_txt
        # destination for seeded contigs prior to final assemblies
        self.final_contigs_dir = final_contigs_dir
        # after faux genome construction, store path here
        self.next_reference_path = next_reference_path
        # where to put the combined contigs at the end:
        self.assembled_seeds = assembled_seeds
        # a logger
        self.logger = logger
        self.check_mands()
        self.attach_genome_seqRecords()  # this method comes first,
        self.write_fasta_genome()  # because this method relies on it
        self.make_map_paths_and_dir()
        # self.make_initial_mapping_ob()

    def check_mands(self):
        """ checks that all mandatory arguments are not none
        """
        mandatory = [self.genbank_path, self.max_iterations,
                     self.output_root, self.clustered_loci_txt]
        if any([x is None for x in mandatory]):
            raise ValueError("SeedGenome must be instantiated with at least "
                             "genbank_path, max_iterations, cluster file, " +
                             "and output_root")

    def make_map_paths_and_dir(self):
        """ Given a output root, this prepares all the needed subdirs and paths
        """
        self.iter_mapping_list = []
        for i in range(0, self.max_iterations):
            self.iter_mapping_list.append(LociMapping(
                name="{0}_mapping_for_iter_{1}".format(self.name, i),
                iteration=i,
                mapping_subdir=os.path.join(
                    self.output_root,
                    "{0}_mapping_for_iter_{1}".format(self.name, i)),
                assembly_subdir_needed=False))
        if self.final_contigs_dir is None:
            self.final_contigs_dir = os.path.join(self.output_root,
                                                  "final_contigs")
        if not os.path.isdir(self.final_contigs_dir):
            os.makedirs(self.final_contigs_dir)

    def write_fasta_genome(self):
        """Given a genbank file, this writes out as (multi)fasta
        """
        self.name = os.path.splitext(
            os.path.basename(self.genbank_path))[0]
        self.ref_fasta = os.path.join(self.output_root,
                                      str(self.name + ".fasta"))
        with open(self.genbank_path, 'r') as fh:
            with open(self.ref_fasta, 'w') as outfh:
                sequences = SeqIO.parse(fh, "genbank")
                count = SeqIO.write(sequences, outfh, "fasta")
        assert count == len(self.seq_records), "Error parsing genbank file!"
                # print("re-wrote %i sequences as fasta" % count)

    def attach_genome_seqRecords(self):
        """attach a list of seqrecords
        """
        with open(self.genbank_path, 'r') as fh:
            self.seq_records = list(SeqIO.parse(fh, "genbank"))


class ngsLib(object):
    """paired end data object
    This is made post mapping, prior to extraction.
    """
    def __init__(self, name, master=False, readF=None, readR=None,
                 readS0=None, readS1=None, mapping_success=False,
                 smalt_dist_path=None, readlen=None,
                 libtype=None, logger=None, smalt_exe=None,
                 ref_fasta=None):
        self.name = name
        # Bool: whether this is a master record
        self.master = master
        # holds libtype detected from non-None libraries
        self.libtype = libtype  # set this dynamically
        # forward Fastq path
        self.readF = readF
        # reverse Fastq path
        self.readR = readR
        # singleton Fastq path
        self.readS0 = readS0
        # other singleton fastq path (thining ahead, but not really...)
        self.readS1 = readS1
        # detected from Forward fastq with gget_readlen below
        self.readlen = readlen  # set this dynamically
        # bool: did mapping have errors?
        self.mapping_success = mapping_success
        # needed to generate distance file if master
        self.smalt_exe = smalt_exe
        # also needed to generate distance file if master
        self.ref_fasta = ref_fasta
        # results of distance mapping
        self.smalt_dist_path = smalt_dist_path  # set this dynamically
        self.logger = logger
        self.check_mands()
        self.set_libtype()
        self.get_readlen()
        self.smalt_insert_file()

    def check_mands(self):
        """ checks that all mandatory arguments are not none
        """
        mandatory = [self.name, self.readF, self.readR, self.ref_fasta]
        if any([x is None for x in mandatory]):
            raise ValueError("SeedGenome must be instantiated with name, "
                             "forward reads, reverse reads, and ref fasta")

    def set_libtype(self):
        """sets to either s_1, pe, pe_s
        TODO: add mate support and support for multiple single libraries
        """
        if self.readF is None:
            if self.readR is None:
                if self.readS0 is not None:
                    self.libtype = "s_1"
                else:
                    raise ValueError("cannot set library type")
            else:
                raise ValueError("cannot set library type from one PE read")
        else:
            if self.readS0 is not None:
                self.libtype = "pe_s"
            else:
                self.libtype = "pe"

    def get_readlen(self):
        if self.master is not True:
            return None
        if self.libtype in ['pe', 'pe_s']:
            self.readlen = get_ave_read_len_from_fastq(
                self.readF, N=36, logger=self.logger)
        else:
            self.readlen = get_ave_read_len_from_fastq(
                self.readS0, N=36, logger=self.logger)

    def smalt_insert_file(self):
        # if intermediate mapping data, not original datasets
        if self.master is not True:
            return None
        if self.libtype in ['pe', 'pe_s']:
            self.smalt_dist_path = estimate_distances_smalt(
                outfile=os.path.join(os.path.dirname(self.ref_fasta),
                                     "smalt_distance_est.sam"),
                smalt_exe=self.smalt_exe,
                ref_genome=self.ref_fasta,
                fastq1=self.readF, fastq2=self.readR,
                logger=self.logger)
        else:
            print("cannot create distance estimate for lib type %s" %
                  self.libtype)
            return None


class LociMapping(object):
    """
    instantiate with iteration, mapping subdir, and ref
    map_ref_genome_will use it and an ngslib for mapping
    extract_
    order of operations: map to reference, extract and convert,
    assemble, save results here
    """
    def __init__(self, name, iteration, mapping_subdir, mapping_prefix=None,
                 mapping_success=False, assembly_success=False,
                 ref_fasta=None, pe_map_bam=None, s_map_bam=None,
                 sorted_mapped_bam=None, merge_map_sam=None, mapped_bam=None,
                 mapped_sam=None, spades_subdir=None,
                 unmapped_sam=None, mappedF=None, mappedR=None,
                 mapped_ids_txt=None, unmapped_ids_txt=None, unmapped_bam=None,
                 mappedS=None, assembled_contig=None, assembly_subdir=None,
                 unmappedF=None, unmappedR=None, unmappedS=None,
                 mapped_ngsLib=None, unmapped_ngsLib=None,
                 assembly_subdir_needed=True):
        # int: current iteration (0 is initial)
        self.iteration = iteration
        self.name = name
        # bool: did the eassembly run without errors
        self.assembly_success = assembly_success
        # results for the
        self.mapping_subdir = mapping_subdir
        self.assembly_subdir = assembly_subdir
        self.assembly_subdir_needed = assembly_subdir_needed
        self.mapping_prefix = mapping_prefix  # set dynamically
        self.ref_fasta = ref_fasta
        #### do not ever name these directly
        self.pe_map_bam = pe_map_bam  # all reads from pe mapping
        self.s_map_bam = s_map_bam  # all reads from singltons mapping
        self.mapped_sam = mapped_sam  # mapped reads only, sam
        self.mapped_bam = mapped_bam  # mapped reads only, bam
        self.mapped_ids_txt = mapped_ids_txt
        self.unmapped_ids_txt = unmapped_ids_txt
        self.unmapped_sam = unmapped_sam
        self.unmapped_bam = unmapped_bam
        self.sorted_mapped_bam = sorted_mapped_bam   # used with intial mapping

        self.mapped_ngsLib = mapped_ngsLib
        self.unmapped_ngsLib = unmapped_ngsLib
        self.assembled_contig = assembled_contig
        ###
        self.check_mands()
        self.make_mapping_subdir_and_prefix()
        self.make_assembly_subdir()
        self.name_bams_and_sams()

    def check_mands(self):
        """ checks that all mandatory arguments are not none
        """
        mandatory = [self.name, self.iteration, self.mapping_subdir]
        if any([x is None for x in mandatory]):
            raise ValueError("mapping ob must be instantiated with name, "
                             "iteration, mapping_subdir name")

    def make_mapping_subdir_and_prefix(self):
        if self.mapping_subdir is None:
            pass
        else:
            if not os.path.isdir(self.mapping_subdir):
                os.makedirs(self.mapping_subdir)
            else:
                pass
            self.mapping_prefix = os.path.join(
                self.mapping_subdir,
                "{0}_iteration_{1}".format(self.name, self.iteration))

    def name_bams_and_sams(self):
        self.pe_map_bam = str(self.mapping_prefix + "_pe.bam")
        self.s_map_bam = str(self.mapping_prefix + "_s.bam")
        self.mapped_bam = str(self.mapping_prefix + ".bam")
        self.unampped_bam = str(self.mapping_prefix + "unmapped.bam")
        self.sorted_mapped_bam = str(self.mapping_prefix + "_sorted.bam")
        self.mapped_sam = str(self.mapping_prefix + ".sam")
        self.unmapped_sam = str(self.mapping_prefix + "_unmapped.sam")
        self.unmapped_ids_txt = str(self.mapping_prefix + "_unmapped.txt")
        self.mapped_ids_txt = str(self.mapping_prefix + "_mapped.txt")

    def make_assembly_subdir(self):
        if self.assembly_subdir_needed:
            if self.assembly_subdir is not None:
                if not os.path.isdir(self.assembly_subdir):
                    os.makedirs(self.assembly_subdir)
        else:
            pass


#################################### functions ###############################


def get_args():  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Given regions from riboSnag, assembles the mapped reads",
        add_help=False)  # to allow for custom help
    # parser.add_argument("seed_dir", action="store",
    #                     help="path to roboSnag results directory")
    parser.add_argument("clustered_loci_txt", action="store",
                        help="output from riboSelect")

    # taking a hint from http://stackoverflow.com/questions/24180527
    requiredNamed = parser.add_argument_group('required named arguments')
    requiredNamed.add_argument("-F", "--fastq1", dest='fastq1', action="store",
                               help="forward fastq reads, can be compressed",
                               type=str, default="", required=True)
    requiredNamed.add_argument("-R", "--fastq2", dest='fastq2', action="store",
                               help="reverse fastq reads, can be compressed",
                               type=str, default="", required=True)
    requiredNamed.add_argument("-r", "--reference_genbank",
                               dest='reference_genbank',
                               action="store", default='', type=str,
                               help="fasta reference, used to estimate " +
                               "insert sizes, and compare with QUAST",
                               required=True)
    requiredNamed.add_argument("-o", "--output", dest='output', action="store",
                               help="output directory; " +
                               "default: %(default)s", default=os.getcwd(),
                               type=str, required=True)

    # had to make this faux "optional" parse so that the named required ones
    # above get listed first
    optional = parser.add_argument_group('optional arguments')
    optional.add_argument("-S", "--fastq_single", dest='fastqS',
                          action="store",
                          help="single fastq reads", type=str, default=None)
    optional.add_argument("-n", "--experiment_name", dest='exp_name',
                          action="store",
                          help="prefix for results files; " +
                          "default: %(default)s",
                          default="riboSeed", type=str)
    optional.add_argument("-l", "--flanking_length",
                          help="length of flanking regions, can be colon-" +
                          "separated to give separate upstream and " +
                          "downstream flanking regions; default: %(default)s",
                          default='1000', type=str, dest="flanking")
    optional.add_argument("-m", "--method_for_map", dest='method',
                          action="store",
                          help="available mappers: smalt; " +
                          "default: %(default)s",
                          default='smalt', type=str)
    optional.add_argument("-c", "--cores", dest='cores', action="store",
                          default=None, type=int,
                          help="cores for multiprocessing workers" +
                          "; default: %(default)s")
    optional.add_argument("-k", "--kmers", dest='kmers', action="store",
                          default="21,33,55,77,99,127", type=str,
                          help="kmers used for final assembly" +
                          ", separated by commas; default: %(default)s")
    optional.add_argument("-p", "--pre_kmers", dest='pre_kmers',
                          action="store",
                          default="21,33,55", type=str,
                          help="kmers used during seeding assemblies, " +
                          "separated bt commas" +
                          "; default: %(default)s")
    optional.add_argument("-g", "--min_growth", dest='min_growth',
                          action="store",
                          default=0, type=int,
                          help="skip remaining iterations if contig doesnt " +
                          "extend by --min_growth. if 0, ignore" +
                          "; default: %(default)s")
    optional.add_argument("-s", "--min_score_SMALT", dest='min_score_SMALT',
                          action="store",
                          default=None, type=int,
                          help="min score forsmalt mapping; inferred from " +
                          "read length" +
                          "; default: inferred")
    optional.add_argument("--include_shorts", dest='include_short_contigs',
                          action="store_true",
                          default=False,
                          help="if assembled contig is smaller than  " +
                          "--min_assembly_len, contig will still be included" +
                          " in assembly; default: inferred")
    optional.add_argument("-a", "--min_assembly_len", dest='min_assembly_len',
                          action="store",
                          default=6000, type=int,
                          help="if initial SPAdes assembly largest contig " +
                          "is not at least as long as --min_assembly_len, " +
                          "exit. Set this to the length of the seed " +
                          "sequence; if it is not achieved, seeding across " +
                          "regions will likely fail; default: %(default)s")
    optional.add_argument("--paired_inference", dest='paired_inference',
                          action="store_true", default=False,
                          help="if --paired_inference, mapped read's " +
                          "pairs are included; default: %(default)s")
    optional.add_argument("--subtract", dest='subtract', action="store_true",
                          default=False,
                          help="if --subtract, reads aligned " +
                          "to each reference will not be aligned to future " +
                          "iterations.  Probably you shouldnt do this" +
                          "unless you really happen to want to")
    optional.add_argument("--circular",
                          help="if the genome is known to be circular, and " +
                          "an region of interest (including flanking bits) " +
                          "extends past chromosome end, this extends the " +
                          "seqence past chromosome origin forward by 5kb; " +
                          "default: %(default)s",
                          default=False, dest="circular", action="store_true")
    optional.add_argument("--padding", dest='padding', action="store",
                          default=5000, type=int,
                          help="if treating as circular, this controls the " +
                          "length of sequence added to the 5' and 3' ends " +
                          "to allow for selecting regions that cross the " +
                          "chromosom's origin; default: %(default)s")
    optional.add_argument("--keep_unmapped", dest='keep_unmapped',
                          action="store_true", default=False,
                          help="if --keep_unmapped, fastqs are generated " +
                          "containing unmapped reads; default: %(default)s")
    optional.add_argument("--ref_as_contig", dest='ref_as_contig',
                          action="store", default="untrusted", type=str,
                          choices=["None", "trusted", "untrusted"],
                          help="if 'trusted', SPAdes will  use the seed " +
                          "sequences as a --trusted-contig; if 'untrusted', " +
                          "SPAdes will treat as --untrusted-contig. if '', " +
                          "seeds will not be used during assembly. " +
                          "See SPAdes docs; default: %(default)s")
    optional.add_argument("--no_temps", dest='no_temps', action="store_true",
                          default=False,
                          help="if --no_temps, mapping files will be " +
                          "removed after all iterations completed; " +
                          " default: %(default)s")
    optional.add_argument("--skip_control", dest='skip_control',
                          action="store_true",
                          default=False,
                          help="if --skip_control, no SPAdes-only de novo " +
                          "assembly will be done; default: %(default)s")
    optional.add_argument("-i", "--iterations", dest='iterations',
                          action="store",
                          default=3, type=int,
                          help="if iterations>1, multiple seedings will " +
                          "occur after assembly of seed regions; " +
                          "if setting --target_len, seedings will continue " +
                          "until --iterations are completed or target_len"
                          " is matched or exceeded; " +
                          "default: %(default)s")
    optional.add_argument("-v", "--verbosity", dest='verbosity',
                          action="store",
                          default=2, type=int, choices=[1, 2, 3, 4, 5],
                          help="Logger writes debug to file in output dir; " +
                          "this sets verbosity level sent to stderr. " +
                          " 1 = debug(), 2 = info(), 3 = warning(), " +
                          "4 = error() and 5 = critical(); " +
                          "default: %(default)s")
    optional.add_argument("--target_len", dest='target_len', action="store",
                          default=None, type=float,
                          help="if set, iterations will continue until " +
                          "contigs reach this length, or max iterations (" +
                          "set by --iterations) have been completed. Set as " +
                          "fraction of original seed length by giving a " +
                          "decimal between 0 and 5, or set as an absolute " +
                          "number of base pairs by giving an integer greater" +
                          " than 50. Not used by default")
    optional.add_argument("--DEBUG", dest='DEBUG', action="store_true",
                          default=False,
                          help="if --DEBUG, test data will be " +
                          "used; default: %(default)s")
    optional.add_argument("--DEBUG_multi", dest='DEBUG_multiprocessing',
                          action="store_true",
                          default=False,
                          help="if --DEBUG_multiprocessing, runs seeding in " +
                          "single loop instead of a multiprocessing pool" +
                          ": %(default)s")
    optional.add_argument("--smalt_scoring", dest='smalt_scoring',
                          action="store",
                          default="match=1,subst=-4,gapopen=-4,gapext=-3",
                          help="submit custom smalt scoring via smalt -S " +
                          "scorespec option; default: %(default)s")
    # had to make this explicitly to call it a faux optional arg
    optional.add_argument("-h", "--help",
                          action="help", default=argparse.SUPPRESS,
                          help="Displays this help message")

    ##TODO  Make these check a config file
    optional.add_argument("--spades_exe", dest="spades_exe",
                          action="store", default="spades.py",
                          help="Path to SPAdes executable; " +
                          "default: %(default)s")
    optional.add_argument("--samtools_exe", dest="samtools_exe",
                          action="store", default="samtools",
                          help="Path to samtools executable; " +
                          "default: %(default)s")
    optional.add_argument("--smalt_exe", dest="smalt_exe",
                          action="store", default="smalt",
                          help="Path to smalt executable;" +
                          " default: %(default)s")
    optional.add_argument("--quast_exe", dest="quast_exe",
                          action="store", default="quast.py",
                          help="Path to quast executable; " +
                          "default: %(default)s")
    optional.add_argument("--quast_python_exe", dest="quast_python_exe",
                          action="store", default="python2.7",
                          help="Path to quast executable; " +
                          "default: %(default)s")
    args = parser.parse_args()
    return args

########################  funtions adapted from elsewhere ###################


def check_smalt_full_install(smalt_exe, logger=None):
    smalttestdir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "sample_data",
                                "smalt_test", "")
    if logger is None:
        raise ValueError("Must Use Logging")
    logger.debug("looking for smalt test dir: {0}".format(
        smalttestdir))
    if not os.path.exists(smalttestdir):
        raise FileNotFoundError("cannot find smalt_test dir containing " +
                                "files to verify bambamc install!")
    ref = os.path.join(smalttestdir, "ref_to_test_bambamc.fasta")
    index = os.path.join(smalttestdir, "test_index")
    test_bam = os.path.join(smalttestdir, "test_mapping.bam")
    test_reads = os.path.join(smalttestdir, "reads_to_test_bambamc.fastq")
    testindexcmd = str("{0} index {1} {2}".format(smalt_exe, index, ref))
    testmapcmd = str("{0} map -f bam -o {1} {2} {3}".format(smalt_exe,
                                                            test_bam,
                                                            index,
                                                            test_reads))
    return([testindexcmd, testmapcmd])
    # logger.debug("testing instalation of smalt and bambamc")
    # for i in [testindexcmd, testmapcmd]:
    #     try:
    #         logger.debug(i)
    #         subprocess.run([i],
    #                        shell=sys.platform != "win32",
    #                        stdout=subprocess.PIPE,
    #                        stderr=subprocess.PIPE,
    #                        check=True)
    #     except:
    #         raise ValueError("Error running test to check bambamc lib is " +
    #                          "installed! See github.com/gt1/bambamc " +
    #                          "and the smalt install guide for more details." +
    #                          "https://sourceforge.net/projects/smalt/files/")
    # os.remove(test_bam)
    # os.remove(str(index + ".sma"))
    # os.remove(str(index + ".smi"))


def estimate_distances_smalt(outfile, smalt_exe, ref_genome,
                             fastq1, fastq2, cores=None, logger=None):
    """Given fastq pair and a reference, returns path to distance estimations
    used by smalt to help later with mapping. if one already exists,
    return path to it.
    """
    if cores is None:
        cores = multiprocessing.cpu_count()
    if not os.path.exists(outfile):
        # Index reference for sampling to get PE distances
        if logger:
            logger.info("Estimating insert distances with SMALT")
        # index with default params for genome-sized sequence
        refindex_cmd = str(smalt_exe + " index -k {0} -s {1} {2} " +
                           "{3}").format(20, 10, outfile, ref_genome)
        refsample_cmd = str(smalt_exe + " sample -n {0} -o {1} {2} {3} " +
                            "{4}").format(cores,
                                          outfile,
                                          outfile,
                                          fastq1,
                                          fastq2)
        if logger:
            logger.info("Sampling and indexing {0}".format(
                ref_genome))
        for cmd in [refindex_cmd, refsample_cmd]:
            if logger:
                logger.debug("\t command:\n\t {0}".format(cmd))
            subprocess.run(cmd,
                           shell=sys.platform != "win32",
                           stderr=subprocess.PIPE,
                           stdout=subprocess.PIPE,
                           check=True)
    else:
        if logger:
            logger.info("using existing reference file")
        pass
    return outfile


def check_libs_before_mapping(ngsLib, logger=None):
    # sometimes, if no singletons are found, we get an empt file.
    #  this shoudl weed out any empy files
    for f in ["readF", "readR", "readS0"]:
        # ignore if lib is None, as those wont be used anyway
        if getattr(ngsLib, f) is None:
            continue
        # if lib is not none but file is of size 0
        if not os.path.getsize(getattr(ngsLib, f)) > 0:
            logger.warning("read file %s is empty and will not be used " +
                           "for mapping!", f)
            # set to None so mapper will ignore
            setattr(ngsLib, f, None)


def map_to_genome_ref_smalt(mapping_ob, ngsLib, cores,
                            samtools_exe, smalt_exe, score_minimum=None,
                            scoring="match=1,subst=-4,gapopen=-4,gapext=-3",
                            step=3, k=5, logger=None):
    """run smalt based on pased args
    requires at least paired end input, but can handle an additional library
    of singleton reads. Will not work on just singletons
    """
    check_libs_before_mapping(ngsLib, logger=logger)
    logger.info("Mapping reads to reference genome")
    # check min score
    if score_minimum is None:
        score_min = int(ngsLib.readlen * .3)
    else:
        score_min = score_minimum
    logger.debug(str("mapping with smalt using a score min of " +
                     "{0}").format(score_min))
    # index the reference
    cmdindex = str("{0} index -k {1} -s {2} {3} {3}").format(
        smalt_exe, k, step, ngsLib.ref_fasta)
    # map paired end reads to reference index
    cmdmap = str('{0} map -l pe -S {1} ' +
                 '-m {2} -n {3} -g {4} -f bam -o {5} {6} {7} ' +
                 '{8}').format(smalt_exe, scoring,
                               score_min, cores, ngsLib.smalt_dist_path,
                               mapping_ob.pe_map_bam, ngsLib.ref_fasta,
                               ngsLib.readF,
                               ngsLib.readR)

    smaltcommands = [cmdindex, cmdmap]

    # if singletons are present, map those too.  Index is already made
    if ngsLib.readS0 is not None:
        # cmdindexS = str('{0} index -k {1} -s {2} {3} {3}').format(
        #     smalt_exe, k, step, mapping_ob.ref_fasta)
        cmdmapS = str(
            "{0} map -S {1} -m {2} -n {3} -g {4} -f bam -o {5} " +
            "{6} {7}").format(smalt_exe, scoring, score_min, cores,
                              ngsLib.smalt_dist_path, mapping_ob.s_map_bam,
                              ngsLib.ref_fasta, ngsLib.readS0)
        # merge together the singleton and pe reads
        cmdmergeS = '{0} merge -f  {1} {2} {3}'.format(
            samtools_exe, mapping_ob.pe_map_bam,
            mapping_ob.s_map_bam, mapping_ob.mapped_bam)
        smaltcommands.extend([cmdmapS, cmdmergeS])
    else:
        # 'merge', but reallt just converts
        cmdmerge = str("{0} view -bh {1} >" +
                       "{2}").format(samtools_exe, mapping_ob.pe_map_bam, mapping_ob.mapped_bam)
        smaltcommands.extend([cmdmerge])
    logger.info("running SMALT:")
    logger.debug("with the following SMALT commands:")
    for i in smaltcommands:
        logger.debug(i)
        subprocess.run(i, shell=sys.platform != "win32",
                       stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
    # report simgpleton reads mapped
    if ngsLib.readS0 is not None:
        logger.info(str("Singleton mapped reads: " +
                        get_number_mapped(mapping_ob.s_map_bam,
                                          samtools_exe=samtools_exe)))
    # report paired reads mapped
    logger.info(str("PE mapped reads: " +
                    get_number_mapped(mapping_ob.pe_map_bam,
                                      samtools_exe=samtools_exe)))
    logger.info(str("Combined mapped reads: " +
                    get_number_mapped(mapping_ob.mapped_bam,
                                      samtools_exe=samtools_exe)))
    # apparently there have been no errors, so mapping success!
    ngsLib.mapping_success = True


def convert_bams_to_fastq_cmds(mapping_ob, ref_fasta, samtools_exe,
                               which='mapped', source_ext="_sam", logger=None):
    """returns ngslib
    """
    if which not in ['mapped', 'unmapped']:
        raise ValueError("only valid options are mapped and unmapped")
    read_path_dict = {'readF': None, 'readR': None, 'readS': None}
    for key, value in read_path_dict.items():
        read_path_dict[key] = str(os.path.splitext(
            mapping_ob.mapped_bam)[0] + "_" + which + key + '.fastq')
        logger.debug(read_path_dict[key])

    if any([x is None for x in read_path_dict.values()]):
        raise ValueError("Could not properly construct fastq names!")
    if which == 'mapped':
            source_ext = '_bam'

    # samfilter = "{0} fastq {1} -1 {2} -2 {3} -s {4} -0 ./test.fastq".format(
    samfilter = "{0} fastq {1} -1 {2} -2 {3} -s {4}".format(
        samtools_exe,
        getattr(mapping_ob, str(which + source_ext)),
        read_path_dict['readF'],
        read_path_dict['readR'],
        read_path_dict['readS'])
    return([samfilter], ngsLib(name=which, master=False,
                               logger=logger,
                               readF=read_path_dict['readF'],
                               readR=read_path_dict['readR'],
                               readS0=read_path_dict['readS'],
                               ref_fasta=ref_fasta))


def generate_spades_cmd(
        mapping_ob, ngs_ob, ref_as_contig, as_paired=True, addLibs="", prelim=False,
        k="21,33,55,77,99", spades_exe="spades.py", logger=None):
    """return spades command so we can multiprocess the assemblies
    wrapper for common spades setting for long illumina reads
    ref_as_contig should be either blank, 'trusted', or 'untrusted'
    prelim flag is True, only assembly is run, and without coverage corrections
    #TODO
    the seqname variable is used only for renaming the resulting contigs
    during iterative assembly.  It would be nice to inheirit from "ref",
    but that is changed with each iteration. This should probably be addressed
    before next major version change
    """
    if logger is None:
        raise ValueError("this must be used with a logger!")
    kmers = k  # .split[","]
    #  prepare reference, if being used
    if not ref_as_contig is None:
        alt_contig = "--{0}-contigs {1}".format(
            ref_as_contig, mapping_ob.ref_fasta)
    else:
        alt_contig = ''
    # prepare read types, etc
    if as_paired and ngs_ob.readS0 is not None:  # for lib with both
        singles = "--pe1-s {0}".format(ngs_ob.readS)
        pairs = "--pe1-1 {0} --pe1-2 {1} ".format(
            ngs_ob.readF, ngs_ob.readR)
    elif as_paired and ngs_ob.readS0 is None:  # for lib with just PE
        singles = ""
        pairs = "--pe1-1 {0} --pe1-2 {1}".format(
            ngs_ob.readF, ngs_ob.readR)
    # for libraries treating paired ends as two single-end libs
    elif not as_paired and ngs_ob.readS0 is None:
        singles = ''
        pairs = "--pe1-s {0} --pe2-s {1}".format(
            ngs_ob.readF, ngs_ob.readR)
    else:  # for 3 single end libraries
        singles = "--pe3-s {0} ".format(ngs_ob.readS0)
        pairs = str("--pe1-s {0} --pe3-s {1} ".format(
            ngs_ob.readF, ngs_ob.readR))
    reads = str(pairs + singles)
    if prelim:
        prelim_cmd = str(
            "{0} --only-assembler --cov-cutoff off --sc --careful -k {1} " +
            "{2} {3} {4} -o {5}").format(spades_exe, kmers, reads, alt_contig, addLibs,
                                         mapping_ob.assembly_subdir)
        return prelim_cmd
    else:
        spades_cmd = "{0} --careful -k {1} {2} {3} {4} -o {5}".format(
            spades_exe, kmers, reads, alt_contig, addLibs, mapping_ob.assembly_subdir)
        logger.debug("Running the following command:\n{0}".format(spades_cmd))
        return spades_cmd


def get_extract_convert_spades_cmds(mapping_ob, fetch_mates, samtools_exe,
                                    spades_exe, ref_as_contig, logger):
    logger.debug("generating commands to convert bam to fastq and map to ref")
    commands = []
    convert_cmds, new_ngslib = convert_bams_to_fastq_cmds(
        mapping_ob=mapping_ob, samtools_exe=samtools_exe,
        ref_fasta=mapping_ob.ref_fasta, which='mapped', logger=logger)
    commands.append(convert_cmds)
    spades_cmd = generate_spades_cmd(
        mapping_ob=mapping_ob,
        ngs_ob=new_ngslib,
        ref_as_contig='trusted',
        as_paired=False, prelim=True,
        k="21,33,55,77,99",
        spades_exe=spades_exe, logger=logger)
    commands.append(spades_cmd)
    return(commands, new_ngslib)


def evaluate_spades_success(clu, mapping_ob, proceed_to_target, target_len,
                            min_assembly_len, min_growth,
                            include_short_contigs, keep_best_contig=True,
                            seqname='', logger=None):
    """return path to contigs
s    #TODO
    """
    prelog = "{0}-{1}:".format("SEED_cluster", clu.index)
    if logger is None:
        raise ValueError("this must be used with a logger!")
    if seqname == '':
        seqname = os.path.splitext(os.path.basename(mapping_ob.ref_fasta))[0]
    logger.info("checking for the following file: \n{0}".format(
        os.path.join(mapping_ob.assembly_subdir, "contigs.fasta")))
    mapping_ob.assembly_success = output_from_subprocess_exists(
        os.path.join(mapping_ob.assembly_subdir, "contigs.fasta"))
    if keep_best_contig and mapping_ob.assembly_success:
        logger.info("reserving first contig")
        try:
            keep_only_first_contig(
                os.path.join(mapping_ob.assembly_subdir, "contigs.fasta"),
                newname=seqname)
        except Exception as f:
            logger.error(f)
            raise f
    elif not mapping_ob.assembly_success:
        logger.warning("No output from SPAdes this time around")
    else:
        pass
    mapping_ob.assembled_contig = os.path.join(
        mapping_ob.assembly_subdir, "contigs.fasta")
    ########################################################

    prelog = "{0}-{1}-iter{2}:".format("SEED_cluster", clu.index,
                                       clu.mappings[-1].iteration)
    if clu.mappings[-1].iteration == 0:
        logger.info("%s analyzing  initial mapping", prelog)
    seed_len = get_fasta_lengths(mapping_ob.ref_fasta)[0]
    # set proceed_to_target params
    if proceed_to_target:
        if target_len > 0 and 5 > target_len:
            target_seed_len = int(target_len * seed_len)
        elif target_len > 50:
            target_seed_len = int(target_len)
        else:
            logger.error("%s invalid target length provided; must be given " +
                         "as fraction of total length or as an absolute " +
                         "number of base pairs greater than 50", prelog)
            sys.exit(1)
    else:
        pass
    if not clu.mappings[-1].assembly_success:
        logger.warning("%s Assembly failed: no spades output for %s",
                       prelog, os.path.basename(mapping_ob.ref_fasta))
    # compare lengths of reference and freshly assembled contig
    contig_len = get_fasta_lengths(mapping_ob.assembled_contig)[0]
    # contig_len = get_fasta_lengths(mapping_ob.assembled_contig)[0]
    ref_len = get_fasta_lengths(mapping_ob.ref_fasta)[0]
    contig_length_diff = contig_len - ref_len
    logger.info("%s Seed length: %i", prelog, seed_len)
    if proceed_to_target:
        logger.info("Target length: {0}".format(target_seed_len))
    logger.info("%s Length of this iteration's longest contig: %i",
                prelog, contig_len)
    if mapping_ob.iteration != 0:
        logger.info("%s Length of previous longest contig: %i",
                    prelog, ref_len)
        logger.info("%s The new contig differs from the previous " +
                    "iteration by %i bases", prelog, contig_length_diff)
    else:
        logger.info("%s The new contig differs from the reference " +
                    "seed by %i bases", prelog, contig_length_diff)

    # This cuts failing assemblies short
    if min_assembly_len > contig_len:
        logger.warning("The first iteration's assembly's best contig " +
                       "is not greater than length set by " +
                       "--min_assembly_len. Assembly will likely fail if " +
                       "the contig does not meet the length of the seed")
        if include_short_contigs:
            logger.warning("Continuing, but if this occurs for more " +
                           "than one seed, we reccommend  you abort and " +
                           "retry with longer seeds, a different ref, " +
                           "or re-examine the riboSnag clustering")
        else:
            clu.keep_contig = False  # flags contig for exclusion
        clu.continue_iterating = False  # skip remaining iterations
    else:
        pass
    # This is a feature that is supposed to help skip unneccesary
    # iterations. If the difference is negative (new contig is shorter)
    # continue, as this may happen (especially in first mapping if
    # reference is not closely related to Sample), continue to map.
    # If the contig length increases, but not as much as min_growth,
    # skip future iterations
    if contig_length_diff > 0 and contig_length_diff < min_growth and \
       min_growth > 0:  # ie, ignore by default
        logger.info("the length of the new contig was only 0bp changed " +
                    "from previous iteration; skipping future iterations")
        # this_iteration = max_iterations + 1  # skip remaining iterations
    # if continuing til reaching the target lenth of the seed
    elif proceed_to_target and contig_len >= target_seed_len:
        logger.info("target length threshold! has been reached; " +
                    "skipping future iterations")
        clu.continue_iterating = False  # skip remaining iterations
    else:
        # nothing to see here
        clu.continue_iterating = True
    if clu.continue_iterating:
        return seedGenome
    elif not clu.keep_contig:
        return 1
    else:
        try:
            clu.contigs_new_path = copy_file(
                current_file=mapping_ob.assembled_contig,
                dest_dir=final_contigs_dir,
                name=os.path.join(os.path.basename(mapping_ob.assembled_contig),
                                  "cluster_{0}_final_iter_{1}.fasta".format(
                                      clu.index, mapping_ob.iteration)),
                logger=logger)
        except:
            logger.warning("no contigs moved for %s_%i! Check  SPAdes log " +
                           "in results dir if worried", clu.sequence_id,
                           clu.index)
            return seedGenome
    # logger.debug("moved {0} to {1}".format(contigs_path, contigs_new_path))
    # if no_temps:
    #     logger.info("removing temporary files from {0}".format(mapping_dir))
    #     clean_temp_dir(clu.output_root)


def make_quick_quast_table(pathlist, write=False, writedir=None, logger=None):
    """This skips any fields not in first report, for better or worse...
    """
    if logger is None:
        raise ValueError("Logging must be enabled for make_quick_quast_table")
    if not isinstance(pathlist, list):
        logger.warning("paths for quast reports must be in a list!")
        return None
    filelist = pathlist
    logger.debug("Quast reports to combine: %s", str(filelist))
    mainDict = {}
    counter = 0
    for i in filelist:
        if counter == 0:
            try:
                with open(i, "r") as handle:
                    for dex, line in enumerate(handle):
                        row, val = line.strip().split("\t")
                        if dex in [0]:
                            continue  # skip header
                        else:
                            mainDict[row] = [val]
            except Exception:
                raise ValueError("error parsing %s", i)
        else:
            report_list = []
            try:
                with open(i, "r") as handle:
                    for dex, line in enumerate(handle):
                        row, val = line.strip().split("\t")
                        report_list.append([row, val])
                    logger.debug("report list: %s", str(report_list))
                    for k, v in mainDict.items():
                        if k in [x[0] for x in report_list]:
                            mainDict[k].append(
                                str([x[1] for x in
                                     report_list if x[0] == k][0]))
                        else:
                            mainDict[k].append("XX")
            except Exception as e:
                raise e("error parsing %s", i)
        counter = counter + 1
    logger.info(str(mainDict))
    if write:
        if writedir is None:
            logger.warning("no output dir, cannot write!")
            return mainDict
        try:
            with open(os.path.join(
                    writedir, "combined_quast_report.tsv"), "w") as outfile:
                for k, v in sorted(mainDict.items()):
                    logger.debug("{0}\t{1}\n".format(k, str("\t".join(v))))
                    outfile.write("{0}\t{1}\n".format(
                        str(k), str("\t".join(v))))
        except Exception as e:
            raise e
    return mainDict


def partition_mapping(seedGenome, samtools_exe, flank=[0, 0],
                      cluster_list=None, logger=None):
    """ Extract interesting stuff based on coords, not a binary
    mapped/not_mapped condition
    """
    ####    deal with extracting all the reads that mapp to a cluster
    mapped_regions = []
    logger.info("processing mapping for iteration %i",
                seedGenome.this_iteration)
    for cluster in cluster_list:
        mapping_subdir = os.path.join(
            output_root, cluster.cluster_dir_name,
            "{0}_cluster_{1}_mapping_iteration_{2}".format(
                cluster.sequence_id, cluster.index, seedGenome.this_iteration))
        assembly_subdir = os.path.join(
            output_root, cluster.cluster_dir_name,
            "{0}_cluster_{1}_assembly_iteration_{2}".format(
                cluster.sequence_id, cluster.index, seedGenome.this_iteration))

        mapping0 = LociMapping(
            name="{0}_cluster_{1}_iter{2}".format(
                cluster.sequence_id, cluster.index, seedGenome.this_iteration),
            iteration=seedGenome.this_iteration,
            assembly_subdir_needed=True,
            mapping_subdir=mapping_subdir,
            assembly_subdir=assembly_subdir)
        # if first time through, ge tthe global start adn end coords.
        if cluster.global_start_coord is None or cluster.global_end_coord is None:
            if seedGenome.this_iteration != 0:
                raise ValueError("global start and end should be defined previously! Exiting")
            if sorted([x.start_coord for x in cluster.loci_list]) != \
               [x.start_coord for x in cluster.loci_list]:
                logger.warning("Coords are not in increasing order; " +
                               "you've been warned")
            start_list = sorted([x.start_coord for x in cluster.loci_list])
            logger.debug("Start_list: {0}".format(start_list))

            logger.debug("Find coordinates to gather reads from the following coords:")
            for i in cluster.loci_list:
                logger.debug(str(i.__dict__))
            #  This works as long as coords are never in reverse order
            cluster.global_start_coord = min([x.start_coord for
                                              x in cluster.loci_list]) - flank[0]
            # if start is negative, just use 1, the beginning of the sequence
            if cluster.global_start_coord < 1:
                logger.warning(
                    "Caution! Cannot retrieve full flanking region, as " +
                    "the 5' flanking region extends past start of " +
                    "sequence. If this is a problem, try using a smaller " +
                    "--flanking region, and/or if  appropriate, run with " +
                    "--circular.")
                cluster.global_start_coord = 1
            cluster.global_end_coord = max([x.end_coord for
                                            x in cluster.loci_list]) + flank[1]
            if cluster.global_end_coord > len(cluster.seq_record):
                logger.warning(
                    "Caution! Cannot retrieve full flanking region, as " +
                    "the 5' flanking region extends past start of " +
                    "sequence. If this is a problem, try using a smaller " +
                    "--flanking region, and/or if  appropriate, run with " +
                    "--circular.")
                cluster.global_end_coord = len(cluster.seq_record)
            logger.debug("global start and end: %s %s", cluster.global_start_coord,
                         cluster.global_end_coord)
            #  if no the first time though, fuhgetaboudit.
            #  Ie, the coords have been reassigned by the faux_genome function
        else:
            logger.info("using coords from previous iteration:")
            logger.debug("global start for cluster %i: %i", cluster.index, cluster.global_start_coord)
            logger.debug("global end for cluster %i: %i", cluster.index, cluster.global_end_coord)
        logger.warning("Extracting %s to %s from %s",
                       cluster.global_start_coord,
                       cluster.global_end_coord,
                       cluster.seq_record.id)

        cluster.extractedSeqRecord = SeqRecord(
            cluster.seq_record.seq[
                cluster.global_start_coord:
                cluster.global_end_coord])

        mapping0.ref_fasta = os.path.join(mapping0.mapping_subdir,
                                          "extracted_seed_sequence.fasta")
        with open(mapping0.ref_fasta, "w") as writepath:
            SeqIO.write(cluster.extractedSeqRecord, writepath, 'fasta')

        # Prepare for partitioning
        partition_cmds = []
        # sort our source bam
        sort_cmd = str("{0} sort {1} > {2}").format(
            samtools_exe,
            seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_bam,
            seedGenome.iter_mapping_list[seedGenome.this_iteration].sorted_mapped_bam)
        # index it
        index_cmd = str("{0} index {1}").format(
            samtools_exe, seedGenome.iter_mapping_list[seedGenome.this_iteration].sorted_mapped_bam)
        partition_cmds.extend([sort_cmd, index_cmd])
        # define the region to extract
        region_to_extract = "{0}:{1}-{2}".format(
            cluster.sequence_id, cluster.global_start_coord,
            cluster.global_end_coord)
        # make a subser from of reads in that region
        view_cmd = str("{0} view -o {1} {2} {3}").format(
            samtools_exe, mapping0.mapped_bam,
            seedGenome.iter_mapping_list[seedGenome.this_iteration].sorted_mapped_bam,
            region_to_extract)
        partition_cmds.append(view_cmd)
        mapped_regions.append(region_to_extract)
        ### run cmds
        for cmd in partition_cmds:
            logger.debug(cmd)
            subprocess.run([cmd],
                           shell=sys.platform != "win32",
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,
                           check=True)
        # add mapping to cluster's mapping list
        cluster.mappings.append(mapping0)
    logger.info("mapped regions in initial mapping:\n %s",
                "\n".join([x for x in mapped_regions]))
    ########
    # make unmapped for next time around make
    ########
    # make a sam from the global mapping bam
    make_mapped_sam = "{0} view -o {1} -h {2}".format(
        samtools_exe,
        seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_sam,
        seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_bam)
    update_readlist = str("{0} view  {1} -U {2} | cut -f1 >> {3}").format(
        samtools_exe,
        seedGenome.iter_mapping_list[seedGenome.this_iteration].sorted_mapped_bam,
        ' '.join([x for x in mapped_regions]),
        seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_ids_txt)
    uniquify_list = "sort -u {0}".format(
        seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_ids_txt)
    # from the global sam mapping filter out those in the reads_mapped_txt list
    get_unmapped = "LC_ALL=C grep -w -v -F -f {0}  < {1} > {2}".format(
        seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_ids_txt,
        seedGenome.iter_mapping_list[seedGenome.this_iteration].mapped_sam,
        seedGenome.iter_mapping_list[seedGenome.this_iteration].unmapped_sam)
    for cmd in [make_mapped_sam, update_readlist, uniquify_list, get_unmapped]:
        logger.debug(cmd)
        subprocess.run([cmd],
                       shell=sys.platform != "win32",
                       stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE,
                       check=True)


def add_coords_to_clusters(seedGenome, logger=None):
    """
    """
    for cluster in seedGenome.loci_clusters:  # for each cluster of loci
        # get seq record that cluster is  from
        try:
            cluster.seq_record = \
                get_genbank_rec_from_multigb(
                    recordID=cluster.sequence_id,
                    genbank_records=seedGenome.seq_records)
        except Exception as e:
            logger.error(e)
            sys.exit(1)
        # make coord list
        try:
            extract_coords_from_locus(
                cluster=cluster, feature=cluster.feat_of_interest,
                logger=logger)
        except Exception as e:
            logger.error(e)
            sys.exit(1)
        logger.info(str(cluster.__dict__))


def run_final_assemblies(seedGenome, spades_exe, quast_exe, quast_python_exe,
                         skip_control=True,
                         kmers="21,33,55,77,99", logger=None):
    """
    """
    logger.info("\n\nStarting Final Assemblies\n\n")
    quast_reports = []
    spades_quast_cmds = []
    final_list = ["de_fere_novo"]
    if not skip_control:
        final_list.append("de_novo")
    for j in final_list:
        final_mapping = LociMapping(
            iteration=0,
            name=j,
            mapping_subdir="testdirthatshouldntbemade",
            assembly_subdir_needed=True,
            assembly_subdir=os.path.join(
                seedGenome.output_root,
                "final_{0}_assembly".format(j)))
        logging.info("\n\nRunning %s SPAdes \n" % j)
        if j == "de_novo":
            final_mapping.ref_fasta = ''
            assembly_ref_as_contig = None
        elif j == "de_fere_novo":
            final_mapping.ref_fasta = seedGenome.assembled_seeds
            assembly_ref_as_contig = 'trusted'
        else:
            raise ValueError("Only valid cases are de novo and de fere novo!")
        logger.info("Running %s SPAdes" % j)
        spades_cmd = generate_spades_cmd(
            mapping_ob=final_mapping, ngs_ob=seedGenome.master_ngs_ob,
            ref_as_contig=assembly_ref_as_contig, as_paired=True, prelim=False,
            k=kmers, spades_exe=spades_exe, logger=logger)
        spades_quast_cmds.append(spades_cmd)

        ref = str("-R %s" % seedGenome.ref_fasta)
        # quast_cmd = str("{0} {1} {2} {3} -t {4} -o {5}").format(
        quast_cmd = str("{0} {1} {2} {3} -o {4}").format(
            quast_python_exe,
            quast_exe,
            seedGenome.assembled_seeds,
            ref,
            # args.cores,
            os.path.join(seedGenome.output_root, str("quast_" + j)))
        spades_quast_cmds.append(quast_cmd)
        quast_reports.append(os.path.join(seedGenome.output_root,
                                          str("quast_" + j), "report.tsv"))
    return(spades_quast_cmds, quast_reports)


def make_faux_genome(cluster_list, seedGenome, iteration,
                     output_root, nbuff, logger=None):
    """ stictch together viable assembled contigs.  perhaps more importnatly,
    this also re-write thes coords relative to the new "genome"
    """
    logger.info("prepparing extracted region genome for next round of mapping")
    logger.debug("using %i sequences", len(cluster_list))
    nbuffer = "N" * nbuff
    # faux_genome = str("" + nbuffer)
    faux_genome = ""
    counter = 0
    new_seq_name = "{0}_iter_{1}".format(seedGenome.name, iteration)
    for clu in cluster_list:
        if not clu.keep_contig or not clu.continue_iterating:
            pass
        else:
            clu.global_start_coord = len(faux_genome) + nbuff
            with open(clu.mappings[-1].assembled_contig, 'r') as con:
                contig_rec = list(SeqIO.parse(con, 'fasta'))[0]
            faux_genome = str(faux_genome + nbuffer + contig_rec.seq)
            clu.global_end_coord = len(faux_genome)
            # lastly, set cluster name to new sequence name
            clu.sequence_id = new_seq_name
            counter = counter + 1
    if counter == 0:
        logger.warning("No viable contigs for faux genome construction!")
        return 1
    else:
        logger.info("combined %s records as genome for next round of mapping",
                    counter)
    record = SeqRecord(Seq(str(faux_genome + nbuffer),
                           IUPAC.IUPACAmbiguousDNA()),
                       id=new_seq_name)

    outpath = os.path.join(output_root,
                           "iter_{0}_buffered_genome.fasta".format(iteration))
    with open(outpath, 'w') as outf:
        SeqIO.write(record, outf, 'fasta')
    return (outpath, len(record))


if __name__ == "__main__":
    args = get_args()
    # allow user to give relative paths
    output_root = os.path.abspath(os.path.expanduser(args.output))
    try:
        os.makedirs(output_root)
    except OSError:
        raise OSError(str("Output directory already exists"))
    t0 = time.time()
    log_path = os.path.join(output_root,
                            str("{0}_riboSeed_log.txt".format(
                                time.strftime("%Y%m%d%H%M"))))
    logger = set_up_logging(verbosity=args.verbosity,
                            outfile=log_path,
                            name=__name__)
    # # log version of riboSeed, commandline options, and all settings
    logger.info("riboSeed pipeine package version {0}".format(
        PACKAGE_VERSION))

    logger.info("Usage:\n{0}\n".format(" ".join([x for x in sys.argv])))
    logger.debug("All settings used:")
    for k, v in sorted(vars(args).items()):
        logger.debug("{0}: {1}".format(k, v))
    if args.cores is None:
        args.cores = multiprocessing.cpu_count()
        logger.info("Using %i cores", multiprocessing.cpu_count())

    # Cannot set Nonetype objects via commandline directly and I dont want None
    # to be default dehaviour, so here we convert 'None' to None.
    # I have no moral compass
    if args.ref_as_contig == 'None':
        args.ref_as_contig = None
    try:
        flank = [int(x) for x in args.flanking.split(":")]
        if len(flank) == 1:  # if only one value use for both up and downstream
            flank.append(flank[0])
        assert len(flank) == 2
    except:
        raise ValueError("Error parsing flanking value; must either be " +
                         "integer or two colon-seapred integers")

    if args.method not in ["smalt"]:
        logger.error("'smalt' only method currently supported")
        sys.exit(1)
    logger.debug("checking for installations of all required external tools")
    executables = [args.samtools_exe, args.spades_exe, args.quast_exe]
    if args.method == "smalt":
        executables.append(args.smalt_exe)
    else:
        logger.error("Mapping method not found!")
        sys.exit(1)
    logger.debug(str(executables))
    test_ex = [check_installed_tools(x, logger=logger) for x in executables]
    if all(test_ex):
        logger.debug("All needed system executables found!")
        logger.debug(str([shutil.which(i) for i in executables]))

    # hack together a proper executable for quast, as it needs to
    # be run via python2
    args.quast_exe = str(shutil.which(args.quast_exe))
    logger.debug("FULL quast execuatble path: %s", args.quast_exe)
    # check samtools verison
    try:
        samtools_verison = check_version_from_cmd(
            exe='samtools', cmd='', line=3, where='stderr',
            pattern=r"\s*Version: (?P<version>[^(]+)",
            min_version=SAMTOOLS_MIN_VERSION, logger=logger)
    except Exception as e:
        logger.error(e)
        sys.exit(1)
    logger.debug("samtools version: %s", samtools_verison)
    # check bambamc is installed proper if using smalt
    if args.method == "smalt":
        test_smalt_cmds = check_smalt_full_install(smalt_exe=args.smalt_exe, logger=logger)
        logger.info("testing instalation of smalt and bambamc")
        smalttestdir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    "sample_data",
                                    "smalt_test", "")
        test_index = os.path.join(smalttestdir, "test_index")
        test_bam = os.path.join(smalttestdir, "test_mapping.bam")

        for i in test_smalt_cmds:
            try:
                logger.debug(i)
                subprocess.run([i],
                               shell=sys.platform != "win32",
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               check=True)
            except:
                logger.error(
                    "Error running test to check bambamc lib is " +
                    "installed! See github.com/gt1/bambamc " +
                    "and the smalt install guide for more details." +
                    "https://sourceforge.net/projects/smalt/files/")
                sys.exit(1)

        # remove the temp files
        os.remove(test_bam)
        os.remove(str(test_index + ".sma"))
        os.remove(str(test_index + ".smi"))
    else:
        logger.error("Currently, SMALT is the only supported mapper")
        sys.exit(1)

    # check equal length fastq.  This doesnt actually check propper pairs
    if file_len(args.fastq1) != file_len(args.fastq2):
        logger.error("Input Fastq's are of unequal length! Try " +
                     "fixing with this script: " +
                     "github.com/enormandeau/Scripts/fastqCombinePairedEnd.py")
        sys.exit(1)

    # if the target_len is set. set needed params
    if args.target_len is not None:
        if not args.target_len > 0 or not isinstance(args.target_len, float):
            logger.error("--target_len is set to invalid value! Must be a " +
                         "decimal greater than zero, ie where 1.1 would be " +
                         "110% of the original sequence length.")
            sys.exit(1)
        elif args.target_len > 5 and 50 > args.target_len:
            logger.error("We dont reccommend seeding to lengths greater than" +
                         "5x original seed length. Try between 0.5 and 1.5." +
                         "  If you are setting a target number of bases, it " +
                         " must be greater than 50")
            sys.exit(1)
        else:
            proceed_to_target = True
    else:
        proceed_to_target = False
###############################################################################

###############################################################################

# make seedGenome object
    seedGenome = SeedGenome(
        name=os.path.basename(os.path.splitext(args.reference_genbank)[0]),
        # this needs to be zero indexed to access mappings by iter
        this_iteration=0,
        iter_mapping_list=[],
        max_iterations=args.iterations,
        clustered_loci_txt=args.clustered_loci_txt,
        output_root=output_root,
        unmapped_mapping_list=[],
        genbank_path=args.reference_genbank,
        logger=logger)

    seedGenome.iter_mapping_list[0].ref_fasta = seedGenome.ref_fasta
    ### add ngslib object for user supplied NGS data
    # this will automatically generate a distance file because
    # it is a 'master' lib
    seedGenome.master_ngs_ob = ngsLib(
        name="master",
        master=True,
        readF=args.fastq1,
        readR=args.fastq2,
        readS0=args.fastqS,
        logger=logger,
        smalt_exe=args.smalt_exe,
        ref_fasta=seedGenome.ref_fasta)

    # read in riboSelect clusters, make a lociCluster ob for each,
    # which get placed in seedGenome.loci_clusters
    seedGenome.loci_clusters = parse_clustered_loci_file(
        filepath=seedGenome.clustered_loci_txt,
        gb_filepath=seedGenome.genbank_path,
        output_root=output_root,
        padding=args.padding,
        circular=args.circular,
        logger=logger)

    # add coordinates for each locus in lociCluster.loci_list
    add_coords_to_clusters(seedGenome=seedGenome,
                           logger=logger)
    # make first iteration look like future iterations
    seedGenome.next_reference_path = seedGenome.ref_fasta
    #
    for cluster in seedGenome.loci_clusters:
        cluster.master_ngs_ob = seedGenome.master_ngs_ob
#################################################################################
    # now, we need to assemble each mapping object
    # this should exclude any failures
    while seedGenome.this_iteration  < args.iterations:
        logger.info("processing iteration %i", seedGenome.this_iteration)
        logger.debug("with new seed: %s", seedGenome.next_reference_path)
        clusters_to_process = [x for x in seedGenome.loci_clusters if
                               x.continue_iterating and
                               x.keep_contig]
        if len(clusters_to_process) == 0:
            logger.error("No clusters had sufficient mapping! Exiting")
            sys.exit(1)
        logger.warning("clusters excluded from this iteration \n%s",
                       " ".join([str(x.index) for x in
                                 seedGenome.loci_clusters if
                                 x.index not in [y.index for
                                                 y in clusters_to_process]]))
        ####
        if not seedGenome.this_iteration == 0:
            ## sewq seqrecords for the clusters to be gen.next_reference_path
            with open(seedGenome.next_reference_path, 'r') as nextref:
                next_seqrec = list(SeqIO.parse(nextref, 'fasta'))[0]  # next?
            for clu in clusters_to_process:
                clu.seq_record = next_seqrec
            #make new ngslib from unampped reads
            convert_cmds, unmapped_ngsLib = convert_bams_to_fastq_cmds(
                mapping_ob=seedGenome.iter_mapping_list[seedGenome.this_iteration - 1],
                samtools_exe=args.samtools_exe,
                ref_fasta=seedGenome.next_reference_path,  # used to make index cmd
                which='unmapped', logger=logger)
            unmapped_ngsLib.readlen = seedGenome.master_ngs_ob.readlen
            # unmapped_ngsLib.ref_fasta = seedGenome.next_reference_path
            unmapped_ngsLib.smalt_dist_path = seedGenome.master_ngs_ob.smalt_dist_path
            logger.debug("converting unmapped bam into reads:")
            seedGenome.master_ngs_ob.ref_fasta = seedGenome.next_reference_path
            for cmd in convert_cmds:
                logger.debug(cmd)
                subprocess.run([cmd],
                               shell=sys.platform != "win32",
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               check=True)
        else:
            # start with whole lib if first time through
            unmapped_ngsLib = seedGenome.master_ngs_ob
        # Run commands to map to the genome
        map_to_genome_ref_smalt(
            mapping_ob=seedGenome.iter_mapping_list[seedGenome.this_iteration],
            ngsLib=unmapped_ngsLib,
            # ngsLib=seedGenome.master_ngs_ob,
            cores=args.cores,
            samtools_exe=args.samtools_exe,
            smalt_exe=args.smalt_exe,
            score_minimum=None,
            step=3, k=5,
            scoring="match=1,subst=-4,gapopen=-4,gapext=-3",
            logger=logger)
        try:
            partition_mapping(seedGenome=seedGenome,
                              logger=logger,
                              samtools_exe=args.samtools_exe,
                              flank=flank,
                              cluster_list=seedGenome.loci_clusters)
        except Exception as e:
            logger.error("Error while partitioning reads from iteration %i",
                         seedGenome.this_iteration)
            logger.error(e)
            sys.exit(1)
        extract_convert_assemble_cmds = []
        # generate spades cmds (cannot be multiprocessed)
        for cluster in clusters_to_process:
            logger.debug("getting extract convert cmds for %s cluster %i",
                         cluster.sequence_id, cluster.index)
            cmdlist, new_ngslib = get_extract_convert_spades_cmds(
                mapping_ob=cluster.mappings[-1], fetch_mates=False,
                samtools_exe=args.samtools_exe,
                spades_exe=args.spades_exe,
                ref_as_contig=args.ref_as_contig, logger=logger)
            cluster.mappings[-1].mapped_ngslib = new_ngslib
            extract_convert_assemble_cmds.extend(cmdlist)

        # run all those commands!
        logger.warning("running %i cmds", len(extract_convert_assemble_cmds))
        if args.DEBUG_multiprocessing:
            logger.warning("running without multiprocessing!")
            for cmd in extract_convert_assemble_cmds:
                logger.debug(cmd)
                subprocess.run([cmd],
                               shell=sys.platform != "win32",
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               check=True)
        else:
            pool = multiprocessing.Pool(processes=args.cores)
            results = [
                pool.apply_async(subprocess.run,
                                 (cmd,),
                                 {"shell": sys.platform != "win32",
                                  "stdout": subprocess.PIPE,
                                  "stderr": subprocess.PIPE,
                                  "check": True})
                for cmd in extract_convert_assemble_cmds]
            pool.close()
            pool.join()
            reslist = []
            # logger.info(sum([r.get() for r in results]))
            reslist.append([r.get() for r in results])

        ### evaluate mapping (cant be multiprocessed
        for cluster in clusters_to_process:
            evaluate_spades_success(
                clu=cluster,
                include_short_contigs=args.include_short_contigs,
                mapping_ob=cluster.mappings[-1], keep_best_contig=True,
                seqname='', logger=logger,
                min_assembly_len=args.min_assembly_len,
                min_growth=args.min_growth,
                proceed_to_target=proceed_to_target,
                target_len=args.target_len)
        # logger.error(seedGenome.loci_clusters[0].mappings[0].__dict__)
        faux_genome_path, faux_genome_len = make_faux_genome(
            seedGenome=seedGenome,
            iteration=seedGenome.this_iteration,
            output_root=seedGenome.output_root,
            nbuff=10000,
            cluster_list=clusters_to_process,
            logger=logger)

        if faux_genome_path == 1:
            seedGenome.this_iteration = args.iterations
        else:
            logger.info("Length of buffered 'genome' for mapping: %i",
                        faux_genome_len)
        seedGenome.this_iteration = seedGenome.this_iteration + 1
        seedGenome.next_reference_path = faux_genome_path
        if seedGenome.this_iteration + 1 >= args.iterations:
            logger.info("moving on to final assemblies!")
        else:
            logger.info("Moving on to iteration: %i",
                        seedGenome.this_iteration + 1)

    ##################################################################
    logging.info("combinging contigs from %s", seedGenome.final_contigs_dir)
    for clu in [x for x in seedGenome.loci_clusters if x.keep_contig]:
        copy_file(current_file=clu.mappings[-1].assembled_contig,
                  dest_dir=seedGenome.final_contigs_dir,
                  name=str(clu.sequence_id + "_cluster_" + str(clu.index) + ".fasta"),
                  overwrite=False, logger=logger)
    seedGenome.assembled_seeds = combine_contigs(
        contigs_dir=seedGenome.final_contigs_dir,
        contigs_name="riboSeedContigs",
        logger=logger)
    logger.info("Combined Seed Contigs: %s", seedGenome.assembled_seeds)
    logger.info("Time taken to run seeding: %.2fm" % ((time.time() - t0) / 60))
    # run final contigs
    spades_quast_cmds, quast_reports = run_final_assemblies(
        seedGenome=seedGenome, spades_exe=args.spades_exe,
        quast_exe=args.quast_exe, quast_python_exe=args.quast_python_exe,
        skip_control=args.skip_control, kmers=args.kmers, logger=logger)

    if args.DEBUG_multiprocessing:
        logger.warning("running without multiprocessing!")
        for cmd in spades_quast_cmds:
            logger.debug(cmd)
            subprocess.run([cmd],
                           shell=sys.platform != "win32",
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,
                           check=True)
    else:
        # split the processors based on how many quast reports are on the list
        pool = multiprocessing.Pool(processes=int(
            args.cores / len(quast_reports)))
        # nseqs = len(seedGenome.loci_clusters)
        results = [
            pool.apply_async(subprocess.run,
                             (cmd,),
                             {"shell": sys.platform != "win32",
                              "stdout": subprocess.PIPE,
                              "stderr": subprocess.PIPE,
                              "check": True})
            for cmd in spades_quast_cmds]
        pool.close()
        pool.join()
        # logger.info(sum([r.get() for r in results]))
        logger.info([r.get() for r in results])

    ###
    if not args.skip_control:
        logger.debug("writing combined quast reports")
        try:
            quast_comp = make_quick_quast_table(
                quast_reports,
                write=True,
                writedir=seedGenome.output_root,
                logger=logger)
            for k, v in sorted(quast_comp.items()):
                logger.info("{0}: {1}".format(k, "  ".join(v)))
        except Exception as e:
            logger.error("Error writing out combined quast report")
            logger.error(e)
        logger.info("Comparing de novo and de fere novo assemblies:")

    ###
    # Report that we've finished
    logger.info("Done: %s", time.asctime())
    logger.info("riboSeed Assembly: %s", seedGenome.output_root)
    logger.info("Combined Contig Seeds (for validation or alternate " +
                "assembly): %s", seedGenome.assembled_seeds)
    logger.info("Time taken: %.2fm" % ((time.time() - t0) / 60))
