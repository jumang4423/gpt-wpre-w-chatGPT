#!/usr/bin/env python3

import sys
import os
import json
import graphlib
import openai
import uuid
from pyChatGPT import ChatGPT
import backoff
import argparse
# For syntax highlighting
from pygments import highlight, lexers, formatters

config = {
    # TODO: enter chatGPT page session_token
    "session_token": "YOUR SESSION TOKEN GOES HERE",
    "prompts": [
        "explain this function",
        "decompile to python",
        "explain all variables",
        "is this vulnerable?, if so, explain or write exploit in python",
        "any unknowns in this function?",
        "next to do based on this code?",
    ],
}

def prompt_selecter() -> int:
    print('Select a prompt:')
    for i,prompt in enumerate(config['prompts']):
        print(i, ": ",prompt)
    return int(input())

def string2filename(s):
    return ''.join([c if c.isalnum() else '_' for c in s])

DEBUG = False
CurrentConvoId = uuid.uuid4()
whatToAsk = config['prompts'][prompt_selecter()]
api = ChatGPT(config['session_token'], conversation_id=CurrentConvoId)

def clean_decomp(decomp):
    return decomp.strip('\n') + '\n'

# Misc graph util functions
def transitive_deps(func, callgraph):
    deps = set()
    def dfs(func):
        for callee in callgraph[func]:
            if callee not in deps:
                deps.add(callee)
                dfs(callee)
    dfs(func)
    return deps

def subgraph(callgraph, root):
    subgraph = {}
    subgraph[root] = callgraph[root]
    for func in transitive_deps(root, callgraph):
        subgraph[func] = callgraph[func]
    return subgraph

def print_call_tree(root, callgraph, depth=0):
    print('  '*depth + root)
    for callee in callgraph[root]:
        print_call_tree(callee, callgraph, depth+1)

# Custom exception for prompt too long errors so that we can use the
# same function for simulation and actual summarization
class PromptTooLongError(Exception):
    pass

def summarize(text, max_tokens=256):
    completion = ""
    try:
        resp = api.send_message(text)
        completion = resp['message']
        CurrentConvoId = conversation_id
    except:
        print("error while summarizing")
        print("try in 1h later")
        sys.exit(1)

    print("SUMMARY:")
    print(completion)
    return completion

def summarize_short_code(decomp, summaries, callees):
    prompt = ''
    if len(callees) > 0:
        prompt += 'Given the following summaries:\n'
        for callee in callees:
            prompt += f'{callee}: {summaries[callee]}\n'
    prompt += whatToAsk
    prompt += ' in a single sentence:\n'
    prompt += '```\n' + decomp + '\n```\n'
    one_line_summary = summarize(prompt)
    return one_line_summary

def summarize_long_code(decomp, summaries, callees, max_lines=100, strategy='long'):
    codelines = decomp.split('\n')
    base_prompt = ''
    if len(callees) > 0:
        base_prompt += 'Given the following summaries:\n'
        for callee in callees:
            base_prompt += f'{callee}: {summaries[callee]}\n'
    chunk_summaries = []
    for i in range(0, len(codelines), max_lines):
        prompt = base_prompt
        if len(chunk_summaries) > 0:
            prompt += 'And the following summaries of the code leading up to this snipppet:\n'
            for j,chunk_summary in enumerate(chunk_summaries):
                prompt += f'Part {j+1}: {chunk_summary}\n'
        if strategy == 'long':
            prompt += whatToAsk
            prompt += ' in a paragraph:\n'
        elif strategy == 'short':
            prompt += whatToAsk
            prompt += ' in a single sentence:\n'
        else:
            raise ValueError('Invalid strategy')
        prompt += '```\n' + '\n'.join(codelines[i:i+max_lines]) + '\n```\n'
        chunk_summaries.append(
            summarize(prompt, max_tokens=(512 if strategy == 'long' else 256))
        )
    # Summarize the whole thing
    prompt = 'Given the following summaries of the code:\n'
    for i,chunk_summary in enumerate(chunk_summaries):
        prompt += f'Part {i+1}/{len(chunk_summaries)}: {chunk_summary}\n'
    prompt += whatToAsk
    prompt += ' in a single sentence.\n'
    one_line_summary = summarize(prompt)
    return one_line_summary

def summarize_all(topo_order, callgraph, decompilations, max_lines=100, already_summarized=None):
    if already_summarized is None:
        summaries = {}
    else:
        # Make a copy so we don't modify the original
        summaries = already_summarized.copy()

    for func in topo_order:
        if func in summaries:
            continue
        callees = callgraph[func]
        decomp = clean_decomp(decompilations[func])
        # First try to summarize the whole function
        summary = None
        try:
            summary = summarize_short_code(decomp, summaries, callees)
        except PromptTooLongError:
            pass
        # If that fails, try to summarize the function in chunks of max_lines lines,
        # decreasing max_lines until we find a chunk size that works or num_lines gets
        # too small. We try to summarize in paragraphs first, then sentences.
        num_lines = max_lines
        while summary is None:
            try:
                if DEBUG: print(f"Trying to summarize {func} in chunks of {num_lines} lines with paragraphs...")
                summary = summarize_long_code(decomp, summaries, callees, max_lines=num_lines, strategy='long')
            except PromptTooLongError:
                num_lines -= 10
                if num_lines < 10:
                    break
        num_lines = max_lines
        while summary is None:
            try:
                if DEBUG: print(f"Trying to summarize {func} in chunks of {num_lines} lines with sentences...")
                summary = summarize_long_code(decomp, summaries, callees, max_lines=num_lines, strategy='short')
            except PromptTooLongError:
                num_lines -= 10
                if num_lines < 10:
                    break
        if summary is None:
            break
        summaries[func] = summary
        yield { func: summary }

# Note: using Dec 2022 OpenAI pricing for davinci: $0.0200  / 1K tokens
TOKEN_PRICE_PER_K_CENTS = 2
MODEL_MAX_TOKENS = 4096
DUMMY_SHORT_SUMMARY = 'This function checks a value in a given location and, if it meets a certain condition, calls a warning function; otherwise, it calls an error function.'
DUMMY_LONG_SUMMARY = 'This code is responsible for validating and initializing an inflate stream. It checks if a given parameter is greater than a limit and calls a warning or error function depending on the result, allocates a block of memory of size param_2 and returns a pointer to it, sets the bits of a uint stored at a different memory address based on the value of a ushort at a specific memory address, checks if a given value is a known sRGB profile and calls a warning or error function depending on the value and parameters, and if valid, sets up the third parameter with a certain value, checks if a read function is valid and computes a CRC32 value for a given input if the parameter is not NULL before producing an error, and reads a specified memory location, checks if a window size is valid, calls a read function, computes a CRC32 value, and stores an error message corresponding to the given parameter.'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--function', required=False, default=None, help='Summarize only this function (and dependencies)')
    parser.add_argument('-d', '--decompilations', required=False, default='decompilations.json')
    parser.add_argument('-g', '--call-graph', required=False, default='call_graph.json')
    parser.add_argument('-o', '--output', required=False, help='Output file (default: progdir/summaries.jsonl)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging')
    parser.add_argument('-l', '--max-lines', type=int, default=100, help='Maximum number of lines to summarize at a time')

    parser.add_argument('progdir')
    args = parser.parse_args()
    progdir = args.progdir
    callgraph = json.load(open(os.path.join(progdir, args.call_graph)))
    decompilations = json.load(open(os.path.join(progdir, args.decompilations)))
    global DEBUG
    DEBUG = args.verbose

    if args.function is not None:
        callgraph = subgraph(callgraph, args.function)
        if args.output is None:
            args.output = f'summaries_{args.function}' + string2filename(whatToAsk) + '.jsonl'
    else:
        if args.output is None:
            args.output = 'summaries_' + string2filename(whatToAsk) + '.jsonl'

    # TODO: handle non-trivial cycles
    topo_order = list(graphlib.TopologicalSorter(callgraph).static_order())

    # Set up highlighting for C
    formatter = formatters.Terminal256Formatter(style='monokai')
    lexer = lexers.get_lexer_by_name('c')
    def debug_summary(func, code, summary):
        print(f"Attempted to summarize {func}:")
        print(highlight(code, lexer, formatter))
        print(f"Callees: {callgraph[func]}")
        print("Summary:")
        print(summary)
        print()

    if args.verbose:
        class FakeTqdm:
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def update(self, *args):
                pass
        tqdm = FakeTqdm
    else:
        from tqdm import tqdm

    # Create the summaries by summarizing leaf functions first, then
    # working our way up the call graph; for non-leaf functions, we
    # use the summaries of the callees to help us summarize the function.
    summaries = {}

    # Load any existing summaries to supoort resuming
    if os.path.exists(os.path.join(progdir, args.output)):
        with open(os.path.join(progdir, args.output)) as f:
            for line in f:
                js = json.loads(line)
                summaries.update(js)

    with open(os.path.join(progdir, args.output), 'a') as f, \
        tqdm(total=len(topo_order)-len(summaries), desc="Summarizing functions") as pbar:
        for summary in summarize_all(topo_order, callgraph, decompilations, max_lines=args.max_lines, already_summarized=summaries):
            summaries.update(summary)
            f.write(json.dumps(summary) + '\n')
            f.flush()

            if args.verbose:
                func_name = list(summary.keys())[0]
                decomp = clean_decomp(decompilations[func_name])
                debug_summary(func_name, decomp, summary)
            else:
                # Only update the progress bar if we're not in verbose mode since in verbose mode
                # the progress bar is fake
                pbar.update(1)
    print(f'Wrote {len(summaries)} summaries to {args.output}.')
    if args.function is not None:
        print(f'Final summary for {args.function}:')
        print(summaries[args.function])

if __name__ == '__main__':
    main()
