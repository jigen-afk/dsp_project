import sys

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        from dspstudio.diagnostics import main
        raise SystemExit(main([arg for arg in sys.argv[1:] if arg!="--self-test"]))
    else:
        from dspstudio.app import main
        raise SystemExit(main())
