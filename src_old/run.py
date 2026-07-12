import sys
import argparse
import os
import re
import json

import textparser
import mongo

import ebooklib
import textract
from ebooklib import epub


def process_arguments(args):
    argparser = argparse.ArgumentParser(description='convert pdfs to text and parse the texts properly')

    argparser.add_argument('--input_path', action='store', help='path to folder with pdf/cbr/cbz/doc/epub/chm/djvu files')
    argparser.add_argument('--output_path', action='store', help='path to save text files to')
    pars = vars(argparser.parse_args(args))
    return pars


def list_dir_recursively(path):
    absolute_path = os.path.abspath(path)
    all_files = []

    for root, subdirs, files in os.walk(absolute_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            all_files.append(file_path)

    return all_files


def remove_tags(text):
    TAG_RE = re.compile(r'<[^>]+>')
    return TAG_RE.sub('', text)


def parse_epub(absolute_path):
    book = epub.read_epub(absolute_path)

    all_text = ''

    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            text = remove_tags(item.get_content().decode('utf-8'))
            all_text += text

    return all_text


def save_lines(outfilename, lines):
    file = open(outfilename, 'w')
    for line in lines:
        file.write(line.string)
        file.write('\n')
    file.flush()
    file.close()


def save_meta(infilename, outfilename, parser_statistics):
    file = open(outfilename, 'w')
    for key, value in parser_statistics.properties.items():
        file.write(';' + key)

    file.write('\n')
    file.write(infilename)

    for key, value in parser_statistics.properties.items():
        file.write(';' + str(value))
    file.flush()
    file.close()


def remove_all_files(folder):
    files_to_delete = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    for file in files_to_delete:
        full_path_to_delete = os.path.abspath(os.path.join(folder + file))
        os.remove(full_path_to_delete)


def get_full_dict(text_parser, infilename, outfilename):
    valid = []
    for line in text_parser.valid_sentences:
        valid.append(line.string)

    faulty = []
    for line in text_parser.faulty_sentences:
        faulty.append(line.string)

    final_dict = {}
    final_dict['valid'] = valid
    final_dict['faulty'] = faulty
    final_dict['meta'] = text_parser.statistic.properties
    final_dict['filename'] = infilename
    return final_dict
    #json_string = json.dumps(final_dict, ensure_ascii=False)
    #outfile = open(outfilename, 'w')
    #outfile.write(json_string)
    #outfile.flush()
    #outfile.close()


def run(params):
    input_path = params['input_path']
    output_path = params['output_path']

    #remove_all_files(output_path)

    parser = textparser.TextParser()

    statistic_filename = os.path.abspath(os.path.join(output_path, 'statistics.txt'))
    print('Saving statistics to ' + statistic_filename)
    statistics_file = open(statistic_filename, 'w')
    statistics_file.write('filename')
    for key, value in parser.statistic.properties.items():
        statistics_file.write(';' + key)
    statistics_file.write('\n')
    statistics_file.close()

    mongo_wrapper = mongo.MongoConnection()

    files = list_dir_recursively(input_path)
    for file in files:
        print('Parsing ' + file)
        was_parsed = True
        path_only, filename_only = os.path.split(file)
        try:

            if not mongo_wrapper.exists(file):
                parser = textparser.TextParser()

                if '.epub' in file:
                    try:
                        text = parse_epub(file)
                        parser.parse(text)
                    except:
                        was_parsed = False

                elif '.pdf' in file or '.doc' in file:
                    try:
                        pr = textract.process(file)
                        if pr:
                            text = pr.decode('utf-8')
                            parser.parse(text)
                        else:
                            was_parsed = False
                    except:
                        was_parsed = False

                else:
                    was_parsed = False

                #elif '.jpg' in file:
                #    text = textract.process(file).decode('utf-8')
                #    parser.parse(text)

                #elif '.djvu' in file:
                #    context = Context()
                #    context.process(file)

                if was_parsed:
                    if len(parser.valid_sentences) > 0:
                        dict = get_full_dict(parser, file, os.path.join(output_path, filename_only + '_parsed.json'))
                        mongo_wrapper.add_book(dict)

                    statistics_file = open(statistic_filename, 'a')
                    statistics_file.write(file)

                    for key, value in parser.statistic.properties.items():
                        statistics_file.write(';' + str(value))
                    statistics_file.write('\n')
                    statistics_file.close()

                    save_lines(os.path.join(output_path, filename_only + '_faulty.txt'), parser.faulty_sentences)
                    save_lines(os.path.join(output_path, filename_only + '_valid.txt'), parser.valid_sentences)
                    save_meta(file, os.path.join(output_path, filename_only + '_meta.txt'), parser.statistic)
        except:
            was_parsed = False

        if not was_parsed:
            _p, _f = os.path.split(file)
            extra_path = _p[len(input_path):]
            create_new = os.path.join(input_path[:-1] + '_failed/', extra_path) + '/'
            print(create_new)
            if not os.path.isdir(create_new):
                os.makedirs(create_new)
            print(create_new + filename_only)
            os.rename(file, create_new + filename_only)

        print(input_path)

if __name__ == "__main__":
    params = process_arguments(sys.argv[1:])
    run(params)

    #parser = textparser.TextParser()
    #parser.parse('The appeal spoken by this phrase cannot be understood in its proper dimension if it is not placed in the horizon of the advent of that uncanniest of all guests of which Nietzsche writes : I describe what is coming , what can no longer come differently : the advent of nihilism .')