import gzip
import logging
import os
import pickle
import re
import tempfile
from heapq import heappush

from vgraph import norm

from pvalue_handler import PvalueHandler

valid_nucleotides = {"A", "T", "G", "C"}

class Gwas:
    def __init__(
        self,
        chrom,
        pos,
        ref,
        alt,
        b,
        se,
        nlog_pval,
        pval,
        n,
        alt_freq,
        dbsnpid,
        ncase,
        imp_info,
        imp_z,
        chr_build37=None,
        pos_build37=None,
        imputed=None,
        vcf_filter="PASS",
    ):

        self.chrom = chrom
        self.pos = pos
        self.ref = ref
        self.alt = alt
        self.b = b
        self.se = se
        self.nlog_pval = nlog_pval
        self.pval = pval
        self.alt_freq = alt_freq
        self.n = n
        self.dbsnpid = dbsnpid
        self.ncase = ncase
        self.imp_info = imp_info
        self.imp_z = imp_z
        self.chr_build37 = chr_build37
        self.pos_build37 = pos_build37
        self.imputed = imputed
        self.vcf_filter = vcf_filter

    def reverse_sign(self):
        ref_old = self.ref
        alt_old = self.alt
        self.ref = alt_old
        self.alt = ref_old
        self.b = self.b * -1

        if self.imp_z is not None:
            self.imp_z = self.imp_z * -1

        try:
            self.alt_freq = 1 - self.alt_freq
        except TypeError:
            self.alt_freq = None

    def check_reference_allele(self, fasta):
        try:
            fasta_ref_seq = fasta.fetch(
                reference=self.chrom,
                start=self.pos - 1,
                end=self.pos + len(self.ref) - 1,
            ).upper()
        except:
            assert 1 == 2
        assert self.ref == fasta_ref_seq

    def normalise(self, fasta, padding=100):
        # TODO handle padding edge cases
        # skip SNVs which do not need trimming
        if len(self.ref) < 2 and len(self.alt) < 2:
            return

        # zero based indexing
        pos0 = self.pos - 1
        # get reference sequence
        seq = fasta.fetch(
            reference=self.chrom, start=pos0 - padding, end=pos0 + padding
        ).upper()
        # left-align and trim alleles
        start, stop, alleles = norm.normalize_alleles(
            seq, padding, padding + len(self.ref), (self.ref, self.alt)
        )
        # set trimmed alleles and new position
        self.ref = alleles[0]
        self.alt = alleles[1]
        self.pos = (pos0 - padding) + start + 1

        # add start base if lost during trimming
        if len(self.ref) == 0 or len(self.alt) == 0:
            # get distance from old and new positions
            dist = (self.pos - 1) - pos0
            # extract base from seq
            left_nucleotide = seq[(padding + dist) - 1 : (padding + dist)]
            # set alleles and pos
            self.ref = left_nucleotide + self.ref
            self.alt = left_nucleotide + self.alt
            self.pos = self.pos - 1

    def update_dbsnp(self, dbsnp):
        if dbsnp is None:
            raise OSError("Could not read dbsnp file")
        self.dbsnpid = None
        for rec in dbsnp.fetch(contig=self.chrom, start=self.pos - 1, stop=self.pos):
            if rec.pos == self.pos:
                self.dbsnpid = rec.id
                break

    def check_alleles_are_valid(self):
        for nucleotide in self.alt:
            assert nucleotide in valid_nucleotides
        for nucleotide in self.ref:
            assert nucleotide in valid_nucleotides

    def __str__(self):
        return str(self.__dict__)

    """ Function to read in GWAS data from plain text file """

    @staticmethod
    def read_from_file(
        input_file_path,
        fasta,
        chrom_col_num,
        pos_col_num,
        ea_col_num,
        nea_col_num,
        effect_col_num,
        se_col_num,
        pval_col_num,
        delimiter,
        header,
        ncase_col_num=None,
        rsid_col_num=None,
        ea_af_col_num=None,
        nea_af_col_num=None,
        imp_z_col_num=None,
        imp_info_col_num=None,
        ncontrol_col_num=None,
        n_col_num=None,
        chr_build37_col_num=None,
        pos_build37_col_num=None,
        imputed_col_num=None,
        alias=None,
        dbsnp=None,
        rejected=None,
    ):

        rsid_pattern = re.compile("^rs[0-9]*$")

        logging.info(f"Reading summary stats and mapping to FASTA: {input_file_path}")
        logging.debug(f"File path: {input_file_path}")
        logging.debug(f"CHR field: {chrom_col_num}")
        logging.debug(f"POS field: {pos_col_num}")
        logging.debug(f"EA field: {ea_col_num}")
        logging.debug(f"NEA field: {nea_col_num}")
        logging.debug(f"Effect field: {effect_col_num}")
        logging.debug(f"SE field: {se_col_num}")
        logging.debug(f"P fields: {pval_col_num}")
        logging.debug(f"Delimiter: {delimiter}")
        logging.debug(f"Header: {header}")
        logging.debug(f"ncase Field: {ncase_col_num}")
        logging.debug(f"dbsnp Field: {rsid_col_num}")
        logging.debug(f"EA AF Field: {ea_af_col_num}")
        logging.debug(f"NEA AF Field: {nea_af_col_num}")
        logging.debug(f"IMP Z Score Field: {imp_z_col_num}")
        logging.debug(f"IMP INFO Field: {imp_info_col_num}")
        logging.debug(f"N Control Field: {ncontrol_col_num}")

        # TODO use namedtuple
        metadata = {
            "TotalVariants": 0,
            "VariantsNotRead": 0,
            "HarmonisedVariants": 0,
            "VariantsNotHarmonised": 0,
            "SwitchedAlleles": 0,
            "NormalisedVariants": 0,
        }
        file_idx = {}
        file_name, file_extension = os.path.splitext(input_file_path)

        if file_extension == ".gz":
            logging.info("Reading gzip file")
            f_handle = gzip.open(input_file_path, "rt")
        else:
            logging.info("Reading plain text file")
            f_handle = open(input_file_path)

        # skip header line (if present)
        if header:
            logging.info(f"Skipping header: {f_handle.readline().strip()}")

        # store results in a serialised temp file to reduce memory usage
        logging.info(f"Temp directory (tempfile.gettempdir()): {tempfile.gettempdir()}")
        results = tempfile.TemporaryFile()
        try:
            logging.info(
                f"Pickled GWAS temp file: {os.readlink(f'/proc/self/fd/{results.fileno()}')}"
            )
        except OSError as exception_name:
            logging.info(f"Could not resolve temp file path via /proc: {exception_name}")

        p_value_handler = PvalueHandler()
        # GWASHub added: list each dropped variant's data line and why (--rejected)
        rejected_handle = open(rejected, "w") if rejected else None

        def reject(reason):
            if rejected_handle:
                rejected_handle.write(f"{metadata['TotalVariants']}\t{reason}\n")

        for line in f_handle:
            metadata["TotalVariants"] += 1
            if metadata["TotalVariants"] % 1_000_000 == 0:
                logging.info(f"Processed {metadata['TotalVariants']:,} variants...")

            columns = line.strip().split(delimiter)

            logging.debug(f"Input row: {columns}")

            try:
                if alias is not None:
                    if columns[chrom_col_num] in alias:
                        chrom = alias[columns[chrom_col_num]]
                    else:
                        chrom = columns[chrom_col_num]
                else:
                    chrom = columns[chrom_col_num]
            except Exception as exception_name:
                logging.debug(f"Skipping {columns}: {exception_name}")
                metadata["VariantsNotRead"] += 1
                reject("not read: chromosome")
                continue

            try:
                pos = int(float(columns[pos_col_num]))  # float is for scientific notation
                assert pos > 0
            except Exception as exception_name:
                logging.debug(f"Skipping {columns}: {exception_name}")
                metadata["VariantsNotRead"] += 1
                reject("not read: position")
                continue

            ref = str(columns[nea_col_num]).strip().upper()
            alt = str(columns[ea_col_num]).strip().upper()

            if ref == alt:
                logging.debug(f"Skipping: ref={ref} is the same as alt={alt}")
                metadata["VariantsNotRead"] += 1
                reject("not read: EA and OA identical")
                continue

            try:
                b = float(columns[effect_col_num])
            except Exception as exception_name:
                logging.debug(f"Skipping {columns}: {exception_name}")
                metadata["VariantsNotRead"] += 1
                reject("not read: BETA")
                continue

            try:
                se = float(columns[se_col_num])
            except Exception as exception_name:
                logging.debug(f"Skipping {columns}: {exception_name}")
                metadata["VariantsNotRead"] += 1
                reject("not read: SE")
                continue

            try:
                pval = p_value_handler.parse_string(columns[pval_col_num])
                nlog_pval = p_value_handler.neg_log_of_decimal(pval)
                pval_float = float(pval)
            except Exception as exception_name:
                logging.debug(f"Skipping line {columns}, {exception_name}")
                metadata["VariantsNotRead"] += 1
                reject("not read: P")
                continue

            try:
                if ea_af_col_num is not None:
                    alt_freq = float(columns[ea_af_col_num])
                elif nea_af_col_num is not None:
                    alt_freq = 1 - float(columns[nea_af_col_num])
                else:
                    alt_freq = None
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse allele frequency: {exception_name}")
                alt_freq = None

            try:
                rsid = columns[rsid_col_num]
                assert rsid_pattern.match(rsid)
            except (IndexError, TypeError, ValueError, AssertionError) as exception_name:
                logging.debug(f"Could not parse dbsnp identifier: {exception_name}")
                rsid = None

            try:
                ncase = float(columns[ncase_col_num])
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse number of cases: {exception_name}")
                ncase = None

            try:
                ncontrol = float(columns[ncontrol_col_num])
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse number of controls: {exception_name}")
                ncontrol = None

            if n_col_num is not None:
                try:
                    n = float(columns[n_col_num])
                except (IndexError, TypeError, ValueError) as exception_name:
                    logging.debug(f"Could not parse total sample size: {exception_name}")
                    n = None
            else:
                try:
                    n = ncase + ncontrol
                except (IndexError, TypeError, ValueError) as exception_name:
                    logging.debug(f"Could not sum cases and controls: {exception_name}")
                    n = ncontrol

            try:
                imp_info = float(columns[imp_info_col_num])
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse imputation INFO: {exception_name}")
                imp_info = None

            try:
                imp_z = float(columns[imp_z_col_num])
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse imputation Z score: {exception_name}")
                imp_z = None

            try:
                _val = columns[chr_build37_col_num] if chr_build37_col_num is not None else None
                chr_build37 = str(_val) if _val not in (None, "", "NA") else None
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse chr_build37: {exception_name}")
                chr_build37 = None

            try:
                _val = columns[pos_build37_col_num] if pos_build37_col_num is not None else None
                pos_build37 = int(_val) if _val not in (None, "", "NA") else None
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse pos_build37: {exception_name}")
                pos_build37 = None

            try:
                _val = columns[imputed_col_num] if imputed_col_num is not None else None
                imputed = str(_val) if _val not in (None, "", "NA") else None
            except (IndexError, TypeError, ValueError) as exception_name:
                logging.debug(f"Could not parse imputed: {exception_name}")
                imputed = None

            result = Gwas(
                chrom,
                pos,
                ref,
                alt,
                b,
                se,
                nlog_pval,
                pval_float,
                n,
                alt_freq,
                rsid,
                ncase,
                imp_info,
                imp_z,
                chr_build37=chr_build37,
                pos_build37=pos_build37,
                imputed=imputed,
            )

            logging.debug(f"Extracted row: {result}")

            # check alleles
            try:
                result.check_alleles_are_valid()
            except AssertionError as exception_name:
                logging.debug(f"Skipping {columns}: {exception_name}")
                metadata["VariantsNotRead"] += 1
                reject("alleles not valid")
                continue

            # harmonise alleles
            try:
                result.check_reference_allele(fasta)
            except AssertionError:
                try:
                    result.reverse_sign()
                    result.check_reference_allele(fasta)
                    metadata["SwitchedAlleles"] += 1
                except AssertionError as exception_name:
                    logging.debug(f"Could not harmonise {columns}: {exception_name}")
                    metadata["VariantsNotHarmonised"] += 1
                    reject("REF does not match the reference genome")
                    continue
            metadata["HarmonisedVariants"] += 1

            # left align and trim variants
            if len(ref) > 1 and len(alt) > 1:
                try:
                    result.normalise(fasta)
                except Exception as exception_name:
                    logging.debug(f"Could not normalise {columns}: {exception_name}")
                    metadata["VariantsNotHarmonised"] += 1
                    reject("could not be normalised")
                    continue
                metadata["NormalisedVariants"] += 1

            # add or update dbSNP identifier
            if dbsnp is not None:
                result.update_dbsnp(dbsnp)

            result.line = metadata["TotalVariants"]  # GWASHub added: for --rejected in Vcf.write_to_file

            # keep file position sorted by chromosome position for recall later
            if result.chrom not in file_idx:
                file_idx[result.chrom] = []
            heappush(file_idx[result.chrom], (result.pos, results.tell()))

            try:
                pickle.dump(result, results)
            except Exception as exception_name:
                logging.error(f"Could not write to {tempfile.gettempdir()}:", exception_name)
                raise exception_name

        f_handle.close()
        if rejected_handle:
            rejected_handle.close()

        logging.info(f'Total variants: {metadata["TotalVariants"]}')
        logging.info(f'Variants could not be read: {metadata["VariantsNotRead"]}')
        logging.info(f'Variants harmonised: {metadata["HarmonisedVariants"]}')
        logging.info(
            f'Variants discarded during harmonisation: {metadata["VariantsNotHarmonised"]}'
        )
        logging.info(f'Alleles switched: {metadata["SwitchedAlleles"]}')
        logging.info(f'Normalised variants: {metadata["NormalisedVariants"]}')
        logging.info(
            f'Skipped {metadata["VariantsNotRead"] + metadata["VariantsNotHarmonised"]} of {metadata["TotalVariants"]}'
        )
        if (metadata["VariantsNotRead"] + metadata["VariantsNotHarmonised"]) / metadata[
            "TotalVariants"
        ] > 0.2:
            logging.warning(
                "More than 20% of variants not read or harmonised. Check your input"
            )

        return results, file_idx, metadata
