# Calibrimbore

Calibrate optical photometric systems to PS1 grizy photometry.

You can pip install calibrimbore as:
```bash
pip install git+https://github.com/CheerfulUser/calibrimbore.git
```

Applying calibrimbore to obervational data requires the master branch of astroquery 
so you will need to clone and install the git repo as follows:
```bash
git clone https://github.com/astropy/astroquery.git
cd astroquery 
pip install .
```
We also make use of [pysynphot](https://pysynphot.readthedocs.io/en/latest/) which uses external data libraries. While calibrimbore 
doesn't use these libraries pysynphot will complain if they are not visible! 

Once it is successfully installed, calibrating any optical filter to PS1 is easy and can be done in just a few steps:
```python
from calibrimbore import sauron
comp = sauron(band='path_to_filter',plot=True,system='ab',gr_lims=[-1,.8],cubic_corr=True)
```
This will go through all the nescissary calibration steps to calculate the linear combination of PS1 filters 
to best replicate the chosen bandpass, calculate a residual cubic colour correction term and calculate 
the extinction vector coefficient as a function of colour according to the Fitzpatrick 99 extinction function.
Key diagnostic figures and equations that define the composite filter are printed if `plot` is `True`.

To get the equations in the ascii string for, you can use the following functions:
```python
comp.ascii_comp()
comp.ascii_cubic_correction()
comp.ascii_R()
```

To save the composite and correction function coefficients for a filter use:
```python
comp.save_transform(name='test_filter',save_fmt='ascii')
```
either ascii or csv can be selected for `save_fmt`.

If you want to calibrate the chosen photometric system you can calculate the composite magnitude for a list 
of sources with 
```python
mags = comp.estimate_mag(ra=ra,dec=dec)
```
where ra and dec are coordinates in degrees of the sources you wish to use for calibration. Calibrimbore will gather PS1 DR2 observations for 
these sources via Vizier and calculate their composite magnitudes. We also account for extinction by calculating the expected extinction for 
stars in a given field using Stellar Locus Regression with the PS1 stellar sources within 0.2 deg of the source.

For more information contact me at: rridden@stsci.edu