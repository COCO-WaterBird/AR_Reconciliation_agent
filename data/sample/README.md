# Sample dataset

This directory holds the sample workbooks that ship with the repository.

The project resolves its input workbooks in this order:

1. `data/input/` - your own workbooks, if you have put them there.
2. `data/sample/` - the sample dataset that comes with the repository.

Right now the sample dataset lives in `data/input/`, because the sample workbooks
are the ones the tests and the CI run against and there is no separate copy of
them.  Putting your own workbooks in `data/input/` therefore means replacing
the sample ones, which is the intended behaviour: the engine must never see
both at once, or it would reconcile against a mixture of two datasets.

If you want to keep the sample data intact while working with your own
workbooks, move the sample files into this directory first:

    mv data/input/Bank_Cash_Receipts.xlsx data/sample/
    mv data/input/Open_AR_Detail.xlsx data/sample/
    # then drop your own files into data/input/

The engine will pick up `data/input/` first and fall back to `data/sample/`.
