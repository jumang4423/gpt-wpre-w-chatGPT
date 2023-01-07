# GPT-WPRE: Whole-program Reverse Engineering with ChatGPT

This is a little toy prototype of a tool that attempts to summarize a whole binary using ChatGPT, based on decompiled code provided by [Ghidra](https://ghidra-sre.org/). -- most programs (and even some functions) are too big to fit all at once.

GPT-WPRE attempts to work around this by recursively creating natural language summaries of a function's dependencies and then providing those as context for the function itself. It's pretty neat when it works! I have tested it on exactly one program, so YMMV.

## limitation

chatGPT doesnt enable us from sending a lot of prompts.
so if you need more power, use gpt3 OR wait for another 1h.

## preparation

1. to configure your session_token(__Secure-next-auth.session-token) of chatGPT, modify recursive_sumarize.py, config["session_token"]

2. resolve dependancies of python

``` console
pip3 install -r requirements.txt
```

3. decompile binary

``` console
$ python3 extract_ghidra_decomp.py

```

4. start completion

``` console
$ python3 recursive_summarize.py ./a.out
```

## Dependencies

You need:
* [Ghidra](https://ghidra-sre.org/)
* [ghidra_bridge](https://github.com/justfoxing/ghidra_bridge) installed and running in the project you want to analyze.
* The Python dependencies, which you can get with `pip install -r requirements.txt`

## Usage

### Call Graph and Decompilation

Make sure the program you want to analyze is open and that the Ghidra bridge server is running. Then extract the control flow graph and decompiled functions with:

```console
$ python extract_ghidra_decomp.py
Building call graph: 100%|██████████████████████████| 651/651 [02:21<00:00,  4.60it/s]
Decompiling functions: 100%|████████████████████████| 651/651 [01:02<00:00, 10.34it/s]
Missing 0 functions:
[]
```

This will create a directory named after the program you're analyzing (e.g., in our example, `libpng16.so.16.38.0_stripped`) with JSON files named `call_graph.json` (for the call graph) and `decompilations.json` for the decompiled functions.

### Summarizing

The script used for this is the creatively named `recursive_summarize.py`. It takes a few arguments:

```console
$ python recursive_summarize.py --help
usage: recursive_summarize.py [-h] [-f FUNCTION] [-d DECOMPILATIONS] [-g CALL_GRAPH]
                              [-o OUTPUT] [-v] [-n] [-l MAX_LINES]
                              progdir

positional arguments:
  progdir

options:
  -h, --help            show this help message and exit
  -f FUNCTION, --function FUNCTION
                        Summarize only this function (and dependencies)
  -d DECOMPILATIONS, --decompilations DECOMPILATIONS
  -g CALL_GRAPH, --call-graph CALL_GRAPH
  -o OUTPUT, --output OUTPUT
                        Output file (default: progdir/summaries.jsonl)
  -v, --verbose         Enable verbose logging
  -l MAX_LINES, --max-lines MAX_LINES
                        Maximum number of lines to summarize at a time
```

Ok. Now to run it:

```console
$ python recursive_summarize.py -f png_read_info libpng16.so.16.38.0_stripped
Summarizing functions: 100%|██████████████████████████| 72/72 [02:30<00:00,  2.09s/it]
Wrote 72 summaries to summaries_png_read_info.jsonl.
Final summary for png_read_info:
This function checks the validity of a pointer, calls the related function, computes a CRC32 checksum, and calls the png_error/png_chunk_warning functions with an error message or warning, as well as setting various parameters for a PNG file.
```

#### Output

The output is a JSON Lines file (`.jsonl`) with one JSON object per function. Each object has one key (the function name) whose value is the summary. For example:

```json
{"FUN_0011b110": "The code checks a given byte value, allocates memory, reads data from a given pointer, checks two arrays for consistency, modifies certain bits from a given parameter, calculates a CRC32 checksum, and calls the png_chunk_benign_error() function with a message based on the zlib return code."}
{"png_read_info": "This function checks parameters for validity, reads data, calculates a CRC32 checksum, allocates memory, and sets values for various parameters."}
```

#### Samples

Sample output for `libpng` is available in the `samples/libpng16.so.16.38.0_stripped` directory.

#### Exploring/Debugging the Summaries

I went a little overboard making this script so I'm including it in the repo even though it would need a bunch more work to be useful for anything other than `libpng`: `extras/debug_summaries.py`. For each function in a `summaries.jsonl` it shows the summary, the original function name (using the debug symbols), an a side-by-side view of the original source code and the decompiled code.

If you want to run it yourself, you need to first get the `libpng` source, then run the script:

```console
$ cd samples/srcs
$ bash clone.sh
[...]
$ cd ../..
$ python extras/debug_summaries.py \
    samples/bins/libpng16.so.16.38.0 \
    samples/libpng16.so.16.38.0_stripped/summaries_png_read_info.jsonl \
    samples/libpng16.so.16.38.0_stripped/decompilations.json
```

Alternatively you can just look at the sample output here: https://moyix.net/~moyix/libpng_png_set_info_summaries.html

### How It Works

GPT-WPRE starts by performing a [topological sort](https://en.wikipedia.org/wiki/Topological_sorting) on the call graph to get a list of functions in the order they should be summarized. It then iteratively summarizes each function, using the summaries of its callees as context. For example, if we have the following call graph and we want to summarize function A:

```
A -> B -------> C
      `--> D --^
```

Then we would start by summarizing C, then D, then B, and finally A. The sequence of prompts looks like:

````
Describe what this function does in a single sentence:
```
int C(double param1) {
    // ...
}
```
````

Giving perhaps a summary like "This function frobnicates the parameter and returns the result." Then we would prompt:

````
Given the following summaries:
C: This function frobnicates the parameter and returns the result.
Describe what this function does in a single sentence:
```
void D(int param1) {
    // Some code that calls C
}
```
````

And so on until we have a summary for A.

#### Summarizing Big Functions

Unfortunately, sometimes even a single function can be too big to summarize in a single prompt when context is included. In this case we need to split up the individual function, summarize its parts, and recombine those summaries.

The *right* way to do this might be do the same topological sort strategy as before but on the function's control flow graph rather than the program's call graph. But a) topological sort isn't defined for graphs with cycles, and whereas mutual recursion is rare at the call graph level, cycles in a control flow graph (aka "loops") are very common; and more prosaically, b) I don't know if Ghidra's decompiler exposes a source-level CFG.

Instead, when we encounter a function that's too big, we split it into sequential chunks of up to 100 lines, summarize each chunk as a paragraph, and then recombine the summaries. If 100 lines is too many, we try 80, 60, ..., down to 20 lines. If this is still too big, we instead summarize the chunks as single sentences.

This all happens in `recursive_summarize.py`'s `summarize_long_code` function.

## Limitations and Future Work

* We don't try to deal with mutual recursion or other non-simple cycles in the call graph, and the topological sort will just throw an exception if it finds a cycle.
* These prompts are the first ones that occurred to me, and probably some prompt engineering would improve the summaries!
* Lots of ways this could be faster, e.g. by batching together requests to summarize things that don't depend on each other. Also pretty sure Ghidra has faster/better ways to get the call graph and decompilation.
