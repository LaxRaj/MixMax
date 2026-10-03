# Drop reference tracks here

A reference is a commercially released song whose tone and loudness you want
to land near. Pick one per genre you're working in.

Use one as the fallback for everything:

    producer render --workspace comparisons --reference references/pop.wav

...or give a single track its own:

    ln -s ../../references/trap.wav comparisons/rap-hook/reference.wav
