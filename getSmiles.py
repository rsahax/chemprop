import pandas as pd 
import argparse 

parser = argparse.ArgumentParser()

parser.add_argument("-f", metavar = 'filename', nargs = 1)

args = parser.parse_args() 
filename = args.f[0]
print(filename)
df = pd.read_csv(filename)
smiles = df['SMILES']
# -4 to remove the .csv and save as plain text
smiles.to_csv(filename[:-4], header=None, index=False)
