import sys
import argparse
import os
import re

import textparser

import ebooklib
import textract
import djvu
import djvu.decode
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


def print_text(sexpr, level=0):
    if level > 0:
        print(' ' * (2 * level - 1), end=' ')
    if isinstance(sexpr, djvu.sexpr.ListExpression):
        if len(sexpr) == 0:
            return
        print(str(sexpr[0].value), [sexpr[i].value for i in range(1, 5)])
        for child in sexpr[5:]:
            print_text(child, level + 1)
    else:
        print(sexpr)


class Context(djvu.decode.Context):

    def handle_message(self, message):
        if isinstance(message, djvu.decode.ErrorMessage):
            print(message, file=sys.stderr)
            # Exceptions in handle_message() are ignored, so sys.exit()
            # wouldn't work here.
            os._exit(1)

    def process(self, path):
        document = self.new_document(djvu.decode.FileURI(path))
        document.decoding_job.wait()
        for page in document.pages:
            page.get_info()
            print_text(page.text.sexpr)


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


def run(params):
    input_path = params['input_path']
    output_path = params['output_path']

    files_to_delete = [f for f in os.listdir(output_path) if os.path.isfile(os.path.join(output_path, f))]
    for file in files_to_delete:
        full_path_to_delete = os.path.abspath(os.path.join(output_path + file))
        os.remove(full_path_to_delete)

    parser = textparser.TextParser()
    statistic_filename = os.path.abspath(os.path.join(output_path, 'statistics.txt'))
    print('Saving statistics to ' + statistic_filename)
    statistics_file = open(statistic_filename, 'w')
    statistics_file.write('filename')
    for key, value in parser.statistic.properties.items():
        statistics_file.write(';' + key)
    statistics_file.write('\n')
    statistics_file.close()


    files = list_dir_recursively(input_path)
    for file in files:
        print('Parsing ' + file)
        parser = textparser.TextParser()

        if '.epub' in file:
            text = parse_epub(file)
            parser.parse(text)

        elif '.pdf' in file:
            text = textract.process(file).decode('utf-8')
            parser.parse(text)

        elif '.doc' in file:
            text = textract.process(file).decode('utf-8')
            parser.parse(text)

        #elif '.jpg' in file:
        #    text = textract.process(file).decode('utf-8')
        #    parser.parse(text)

        #elif '.djvu' in file:
        #    context = Context()
        #    context.process(file)

        statistics_file = open(statistic_filename, 'a')
        statistics_file.write(file)

        for key, value in parser.statistic.properties.items():
            statistics_file.write(';' + str(value))
        statistics_file.write('\n')
        statistics_file.close()

        path, filename = os.path.split(file)
        save_lines(os.path.join(output_path, filename + '_faulty.txt'), parser.faulty_sentences)
        save_lines(os.path.join(output_path, filename + '_valid.txt'), parser.valid_sentences)
        save_meta(file, os.path.join(output_path, filename + '_meta.txt'), parser.statistic)

if __name__ == "__main__":
    params = process_arguments(sys.argv[1:])
    run(params)

    #parser = textparser.TextParser()
    #parser.parse('The appeal spoken by this phrase cannot be understood in its proper dimension if it is not placed in the horizon of the advent of that uncanniest of all guests of which Nietzsche writes : I describe what is coming , what can no longer come differently : the advent of nihilism .')