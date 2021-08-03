import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from scipy.optimize import minimize
from scipy.interpolate import interp1d
import astropy.table as at
import pysynphot as S
from glob import glob 
from astropy.stats import sigma_clip
from scipy.stats import iqr
from extinction import fitzpatrick99, apply
from copy import deepcopy
from scipy.interpolate import UnivariateSpline
from astropy.io import ascii
from astropy.table import Table

import os
package_directory = os.path.dirname(os.path.abspath(__file__)) + '/'

# !!! Need the master branch version of astroquery https://github.com/astropy/astroquery !!!
from .bill import * 
from .R_load import R_val


import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning) 


fig_width_pt = 240.0  # Get this from LaTeX using \showthe\columnwidth
inches_per_pt = 1.0/72.27			   # Convert pt to inches
golden_mean = (np.sqrt(5)-1.0)/2.0		 # Aesthetic ratio
fig_width = fig_width_pt*inches_per_pt  # width in inches

def mag2flux(mag,zp=25):
	"""
	Convert magnitude to flux
	"""
	f = 10**(2/5*(zp-mag))
	return f




class sauron():
	"""
	One System to rule them all, One System to find them, 
	One System to bring them all and in the darkness bind them.


	Creates a composite filter using PS1 filters of the input band.
	This allows for direct comparison between the well calibrated 
	PS1 system and any other optical photometric system. With 
	calibrimbore we can calibrate all systems to PS1. 

	------
	Inputs
	------
	band : `str`
		Path to the file of the bandpass that is to be calibrated 

	name : `str`
		NOT USED name of the filter

	ps1_filters : `str`
		Define which filters are to be used in the composite construction. 
		If `auto` is used then relevant filters will be identified from 
		overlaps. Otherwise give all desired filters e.g. `gr` to use PS1 
		g and r.

	gr_lims : `list`
		Lower and upper g-r colour limits to fit with e.g. `[0.2,0.4]` will 
		restrict it to 0.2<(g-r)<0.4.
	
	gi_lims : `list`
		Lower and upper g-i colour limits to fit with e.g. `[0.2,0.4]` will 
		restrict it to 0.2<(g-i)<0.4.
	
	system : `str`
		Which magnitude system to calibrate to, it can be either `AB` or `Vega`.
	
	plot : `bool`
		Makes diagnostic plots 
	
	make_comp : `bool`
		If `True` the compsite band functions will run.

	cubic_corr : `bool`
		If `True` the a color dependent magnitude cubic polynomial
		correction will be calculated for the composite magnitude.
	"""
	def __init__(self,band=None,name=None,ps1_filters='auto',
				 gr_lims=None,gi_lims=None,system='AB',
				 plot=False,make_comp=True, cubic_corr=True,
				 calc_R=True,obstable=None, savename=None):
		"""
		Setup the class.
		"""
		self.band = band
		self.name = name
		self.zp = None
		self.system = system.lower()
		self.savename = savename
		self.load_band()
		self.ps1_overlap = None
		if ps1_filters.lower() == 'auto':
			self.ps1_filters = ''
			self.filter_overlap()
		else:
			self.ps1_filters = ps1_filters
		
		self.gr_lims = gr_lims
		self.gi_lims = gi_lims
		self.cubic_corr = cubic_corr


		# Defined:
		if self.system == 'ab':
			self.ps1_mags = np.load(package_directory+'data/calspec_ab_mags_ps1.npy',
									allow_pickle=True).item()		
		# actually makes no sense to do this here since all PS1 is in AB
		#elif self.system == 'vega':
		#	self.ps1_mags = np.load(package_directory+'data/calspec_vega_mags_ps1.npy',
		#							allow_pickle=True).item()		

		# Calculated
		self.mags = None
		self.coeff = None
		self.diff = None
		self.mask = None
		self.R = None
		self.spline = None
		self.cubic_coeff = None
		self.gr = self.ps1_mags['g'] - self.ps1_mags['r']
		if self.gr_lims is not None:
			ind = (self.gr > self.gr_lims[0]) & (self.gr < self.gr_lims[1])
			self.gr = self.gr[ind]


		# Identify relevant PS1 filters to use 
		if self.ps1_filters.lower() == 'auto':
			self.filter_overlap()
			print('Making a composite filter with PS1 ' + self.band)

		# Make the composite filter function
		if make_comp:
			if plot:
				self.coverage_plot()
			if obstable is None:
				# calculate the expected Calspec mags in the band
				self.syn_calspec_mags()
			else:
				self.assign_mags(obstable)
			# fit the composite using the expected mags
			self.fit_comp()
			# print the composite function in a nice way
			self.print_comp()
			if cubic_corr:
				# calculate a cubic polynomial to correct color
				self.fit_cubic_correction()
				# print the cubic corrector function in a nice way
				self.print_cubic_correction()
			if plot:
				# make useful plot 
				self.diagnostic_plots()
			if calc_R:
				# calculate the extinction vector coefficients
				self.calculate_R(plot=plot)
			


	def load_band(self):
		"""
		Load in the band to sauron. Can either be a string or a numpy array.
		"""
		# load numpy array
		if type(self.band) == str:
			band = np.loadtxt(self.band)
		# check dims of numpy array
		elif type(self.band) == np.ndarray:
			if band.shape[0] < band.shape[1]:
				band = band.T
		# make a pysynphot bandpass object
		b = S.ArrayBandpass(band[:,0], band[:,1], waveunits='Angstrom',name=self.name)
		self.band = b
		# get the theoretical zeropoint for the loaded bandpass
		self.zp = get_pb_zpt(self.band, reference=self.system, model_mag=0)



	def filter_overlap(self):
		"""
		Find which PS1 filters have significant overlap with the slected bandpass.
		PS1 bands need to cover >1% of the fitting bandpasses area.

		Inputs
		------
		self.band : pysynphot ArrayBandpass
			Band to calibrate

		Returns 
		-------
		self.ps1_filters : `str`
			A single string containing all relevant filters in increasing wavelength size.

		self.ps1_overlap : numpy array
			Percentage overlap for each of the bands
		"""
		bands = ''
		percentage = []
		pbs = list(ps1_bands.keys()) 

		for pb in pbs:
			ps1 = ps1_bands[pb]
			func = interp1d(ps1.wave,ps1.throughput,bounds_error=False,fill_value=0)
			overlap = (np.trapz(func(self.band.wave) * self.band.throughput, x = self.band.wave) / 
					   np.trapz(self.band.throughput, x = self.band.wave))
			percentage += [overlap]
			if overlap > 0.01:
				bands += pb
		if bands == '':
			raise ValueError(('No direct overlap with the PS1 filters! ' + 
							  'A filter combination must be given in the ps1_filters variable.'))
		self.ps1_filters = bands
		self.ps1_overlap = np.array(percentage)


	def syn_calspec_mags(self,ebv=0,Rv=3.1):
		"""
		Calculate magnitudes for each of the Calspec sources using the input bandpass. 

		Inputs
		------
		self.band : pysynphot ArrayBandpass
			Band to calibrate

		ebv : `float` 
			Extinction to be applied to the spectrum in terms of `E(B-V)`
			using the Fitzpatrick 1999 extinction function.
		
		Rv : `float`
			V band extinction vector coefficient, used in apply extinction.
			Assumed to be the standard 3.1.

		Returns
		-------
		mags : array 
			Magnitudes for the input bandpass
		"""
		files = glob(package_directory+'data/calspec/*.dat')
		files = np.array(files)
		# make sure the mags are in the same order
		files.sort()

		mags = []
		for file in files:
			spec = at.Table.read(file, format='ascii')
			spec = S.ArraySpectrum(spec['wave'], spec['flux'], fluxunits='flam',keepneg=True)
			if ebv > 0:
				spec = S.ArraySpectrum(spec.wave, 
								apply(fitzpatrick99(spec.wave.astype('double'),ebv*Rv,Rv),spec.flux))
			mags += [synmag(spec,self.band,self.zp)]
		mags = np.array(mags)
		if ebv == 0:
			self.mags = mags
		else:
			return mags


	def assign_mags(self,obstable):
		"""
		Assign PS1 magnitudes and magnitudes in the fitting filter from an input table.
		
		-----
		Input
		-----
		obstable : dict or dataframe
			Table containing only the observed magnitudes, no errors. Format 
			must have one entry for the filter to be calibrated, the entry name MUST 
			exclude 'ps1'. Entries containing the PS1 magnitudes must have the format of 
			'band' + 'ps1', i.e. 'gps1' for g band. 

		"""
		keys = list(obstable.keys())
		for key in keys:
			if 'ps1' in key.lower():
				self.ps1_mags[key[0]] = obstable[key]
			else:
				self.mags = obstable[key]



	def make_composite(self,coeff=None,mags=None,ext=None):
		"""
		Make composite magnitudes for the input band using the provided PS1 magnitudes.
	
		------
		Inputs
		------
		self.mags / mags : `dict`
			Dictionary containing the PS1 magnitudes in `grizy` bands.

		self.coeff / coeff : `list`
			List of function coefficients in order [g,r,i,z,y,power]
		
		-------
		Returns
		-------
		comp : `array`
			Composite magnitude in the input bandpass.

		"""
		if mags is None:
			ps1_mags = self.ps1_mags
		else:
			ps1_mags = mags
		if ext is not None:
			gr = ps1_mags['g'] - ps1_mags['r']
			Rg, Rg_e = R_val('g',gr=gr)
			extg = Rg*ext
			Rr, Rr_e = R_val('r',gr=gr)
			extr = Rr*ext
			Ri, Ri_e = R_val('i',gr=gr)
			exti = Ri*ext
			Rz, Rz_e = R_val('z',gr=gr)
			extz = Rz*ext
			Ry, Ry_e = R_val('y',gr=gr)
			exty = Ry*ext
		else:
			extg = 0; extr = 0; exti = 0
			extz = 0; exty = 0


		r = 0; z = 0; y= 0	
		g = mag2flux(ps1_mags['g']-extg)
		i = mag2flux(ps1_mags['i']-exti)
		if 'r' in self.ps1_filters:
			r = mag2flux(ps1_mags['r']-extr)
		if 'z' in self.ps1_filters:
			z = mag2flux(ps1_mags['z']-extz)
		if 'y' in self.ps1_filters:
			y = mag2flux(ps1_mags['y']-exty)

		#if coeff is None:
		coeff = self.coeff
		comp = (coeff[0]*g + coeff[1]*r + coeff[2]*i +coeff[3]*z +
				coeff[4]*y)*(g/i)**(coeff[5])

		comp = -2.5*np.log10(comp) + 25 # default PS1 image zeropoint

		if ext is not None:
			if self.R_coeff is None:
				self.calculate_R()
			gr_int = (ps1_mags['g']-extg)-(ps1_mags['r']-extr)
			comp += self.R_vector(x=gr_int)*ext

		if mags is None:
			self.comp = comp
		else:
			return comp

	def comp_minimizer(self,coeff):
		"""
		Function to minimize for fitting filter coefficients.
		
		------
		Inputs
		------
		coeff : `list`
			List of function coefficients in order [g,r,i,z,y,power]

		-------
		Returns
		-------
		res : float
			Sum of the absolute value of the difference between 
			model and composite magnitudes.
		"""
		self.coeff = coeff
		self.make_composite()
		if self.gr_lims is not None:
			ind = (((self.ps1_mags['g'] - self.ps1_mags['r']) > self.gr_lims[0]) & 
					((self.ps1_mags['g'] - self.ps1_mags['r']) < self.gr_lims[1]))
		elif self.gi_lims is not None:
			ind = (((self.ps1_mags['g'] - self.ps1_mags['i']) > self.gr_lims[0]) & 
					((self.ps1_mags['g'] - self.ps1_mags['i']) < self.gr_lims[1]))
		else:
			ind = np.isfinite(self.ps1_mags['g'])

		self.diff = self.mags[ind] - self.comp[ind]
		if self.mask is None:
			res = np.nansum(abs(self.diff))
		else:
			res = np.nansum(abs(self.diff[self.mask]))
		return res

	def _make_c0(self):
		"""
		Set up the initial ceofficient guesses for when the filters are manually defined 
		(its pretty lazy!).
		"""
		c0 = np.array([0,0,0,0,0,0.01])
		if 'g' in self.ps1_filters:
			c0[0] += 0.1
		if 'r' in self.ps1_filters:
			c0[1] += 0.1
		if 'i' in self.ps1_filters:
			c0[2] += 0.1
		if 'z' in self.ps1_filters:
			c0[3] += 0.1
		if 'y' in self.ps1_filters:
			c0[4] += 0.1
		return c0

	def _make_bds(self):
		"""
		Make the boundary for the coefficients. This enforces that 
		all flux contributions are positive and only selected bands 
		are used in the construction.

		------
		Inputs
		------
		self.ps1_filters : `str`
			A single string containing all filters which are to be used

		-------
		Returns
		-------
		bds : `list`
			A list of tupples which define the bounds of the fitted coefficients.		
		"""
		bds = []
		filts = ['g','r','i','z','y']
		for f in filts:
			if f in self.ps1_filters:
				bds += [(0,2)]
			else:
				bds += [(0,1e-10)]

		bds += [(-10,10)]
		return bds

	def fit_comp(self):
		"""
		Minimise the difference between expected and composite magnitudes to get the 
		flux coefficients.
		"""
		try:
			c0 = np.append(self.ps1_overlap,0)
			c0[c0<0.01] = 0
		except:
			c0 = self._make_c0()

		bds = self._make_bds()

		res = minimize(self.comp_minimizer,c0,bounds=bds)
		self.mask = ~sigma_clip(self.diff,sigma=3).mask
		res = minimize(self.comp_minimizer,res.x,bounds=bds)

		self.coeff = res.x 
		self.fit_res = res


	

	def cubic_correction(self,x=None):
		"""
		Standard cubic polynomial
		"""
		if x is None:
			x = self.gr
		coeff = self.cubic_coeff
		fit = coeff[0] + coeff[1] * x + coeff[2] * x**2 + coeff[3] * x**3
		return fit

	def _cube_min_func(self,coeff):
		"""
		Minimizer for the cubic function.
		"""
		self.cubic_coeff = coeff
		y = self.diff[self.mask]

		fit = self.cubic_correction()
		diff = np.nansum((fit[self.mask]-y)**2)
		return abs(diff)

	def fit_cubic_correction(self):
		"""
		Fitting function for the cubic correction polynomial.
		"""
		c0 = [0,0,0,0]
		res = minimize(self._cube_min_func,c0)
		self.cubic_coeff = res.x
		mask = sigma_clip(self.diff-self.cubic_correction(),sigma=3).mask
		self.mask[mask] = False
		res = minimize(self._cube_min_func,c0)
		self.cubic_coeff = res.x


	def print_comp(self):
		"""
		Print the composite flux function in a nice way.
		"""
		from IPython.display import display, Math
		eqn = r'$f_{comp}=\left('

		var = ['f_g','f_r','f_i','f_z','f_y']
		for i in range(5):
			if self.coeff[i] > 0.001:
				eqn += str(np.round(self.coeff[i],3)) + var[i] 
				if (i < 4) & (self.coeff[i+1:-1] > 0.001).any():
					eqn += '+'
		eqn += r'\right)\left( \frac{f_g}{f_i}\right)^{' + str(np.round(self.coeff[5],3)) + '}$'
		display(Math(eqn))

	def ascii_comp(self):
		"""
		Returns the composite flux equation in ascii equation format.
		"""
		eqn = 'f_(comp)=('

		var = ['f_g','f_r','f_i','f_z','f_y']
		for i in range(5):
			if self.coeff[i] > 0.001:
				eqn += str(np.round(self.coeff[i],3)) + var[i] 
				if (i < 4) & (self.coeff[i+1:-1] > 0.001).any():
					eqn += '+'
		eqn += r')(f_g/f_i)^(' + str(np.round(self.coeff[5],3)) + ')'
		return eqn

	def save_transform(self,save_fmt='ascii',name=None):
		coeffs = Table()
		var = ['fg','fr','fi','fz','fy']
		c = self.coeff
		c[abs(c) < 0.001] = 0
		for i in range(5):
			coeffs[var[i]] = np.array([c[i]])
		coeffs['fg/fr'] = np.array([c[5]])

		
		if self.cubic_coeff is not None:
			coeffs['mc_gr'] = np.array([self.cubic_coeff[0]])
			coeffs['mc_gr2'] = np.array([self.cubic_coeff[1]])
			coeffs['mc_gr3'] = np.array([self.cubic_coeff[2]])

		if name is None:
			if self.name is None:
				name = 'coeffs'
			else:
				name = self.name 
		if save_fmt.lower() == 'ascii':
			ascii.write(coeffs, name + '.dat', overwrite=True)
			print('saved as ' + name + '.dat')
		elif save_fmt.lower() == 'csv':
			coeffs = coeffs.to_pandas()
			coeffs.to_csv(name+'.csv',index=False)
			print('saved as ' + name + '.csv')



	def print_cubic_correction(self):
		"""
		Print the cubic correction polynomial with nice formatting.
		"""
		from IPython.display import display, Math
		coeff = self.cubic_coeff
		eqn = r'$m_c=' + str(np.round(coeff[0],3)) 
		if coeff[1] > 0:
			eqn += '+'
		eqn += str(np.round(coeff[1],4)) + '(g-r)' 
		if coeff[2] > 0:
			eqn += '+'
		eqn += str(np.round(coeff[2],4)) + '(g-r)^2' 
		if coeff[3] > 0:
			eqn += '+'
		eqn += str(np.round(coeff[3],4)) + '(g-r)^3'
		display(Math(eqn))

	def ascii_cubic_correction(self):
		"""
		Returns the cubic correction equation in ascii equation format.
		"""
		coeff = self.cubic_coeff
		eqn = 'm_c=' + str(np.round(coeff[0],3)) 
		if coeff[1] > 0:
			eqn += '+'
		eqn += str(np.round(coeff[1],4)) + '(g-r)' 
		if coeff[2] > 0:
			eqn += '+'
		eqn += str(np.round(coeff[2],4)) + '(g-r)^2' 
		if coeff[3] > 0:
			eqn += '+'
		eqn += str(np.round(coeff[3],4)) + '(g-r)^3'
		return eqn

	def coverage_plot(self):
		"""
		Makes a plot showing all PS1 filters and the fitting filter.
		All PS1 filters that are used are shown in solid lines 
		while those not used are shown in dotted lines.
		"""
		plt.figure(figsize=(1.5*fig_width,1*fig_width))

		plt.fill_between(self.band.wave,self.band.throughput/np.nanmax(self.band.throughput),
						 color='k',alpha=0.05)
		plt.plot(self.band.wave,self.band.throughput/np.nanmax(self.band.throughput),
				 color='grey',label='$TESS$')
		colors = ['g','r','k','m','sienna']	
		filts = 'grizy'
		k = 0
		for f in filts:
			if f in self.ps1_filters:
				plt.plot(ps1_bands[f].wave,
						 ps1_bands[f].throughput/np.nanmax(ps1_bands[f].throughput),
						 '-',color=colors[k],label='PS1 '+f)
			else:
				plt.plot(ps1_bands[f].wave,
						 ps1_bands[f].throughput/np.nanmax(ps1_bands[f].throughput),
					 ':',color=colors[k],label='PS1 '+f)
			k += 1
		
		plt.text(4500,1.03,'PS1 $g$',color='g',fontsize=12)
		plt.text(5800,1.03,'PS1 $r$',color='r',fontsize=12)
		plt.text(7150,1.03,'PS1 $i$',color='k',fontsize=12)
		plt.text(8200,1.03,'PS1 $z$',color='m',fontsize=12)
		plt.text(9200,1.03,'PS1 $y$',color='sienna',fontsize=12)

		plt.ylim(0,1.15)

		plt.ylabel('Throughput',fontsize=15)
		plt.xlabel(r'Wavelength $\left(\rm \AA \right)$',fontsize=15)
		plt.tight_layout()

		if self.savename is not None:
			plt.savefig(self.savename + '_coverage.pdf')


	def diagnostic_plots(self,spline=True):
		"""
		Plots to show how good the fit is.
		"""
		
		gi = False
		# apply colour limits
		if self.gr_lims is not None:
			ind = (((self.ps1_mags['g'] - self.ps1_mags['r']) > self.gr_lims[0]) & 
					((self.ps1_mags['g'] - self.ps1_mags['r']) < self.gr_lims[1]))
			x = (self.ps1_mags['g'] - self.ps1_mags['r'])[ind]
			gi = False
		elif self.gi_lims is not None:
			ind = (((self.ps1_mags['g'] - self.ps1_mags['i']) > self.gr_lims[0]) & 
					((self.ps1_mags['g'] - self.ps1_mags['i']) < self.gr_lims[1]))
			x = (self.ps1_mags['g'] - self.ps1_mags['i'])[ind]
			gi = True
		else:
			x = (self.ps1_mags['g'] - self.ps1_mags['r'])
			ind = np.isfinite(x)

		self.make_composite()
		self.diff = (self.mags - self.comp)[ind]
		self.mask = ~sigma_clip(self.diff,sigma=3).mask
		 
		diff = (self.diff[self.mask])*1e3# - self.spline(x[self.mask])) * 1e3

		med = np.percentile(diff,50)
		low = np.percentile(diff,16)
		high = np.percentile(diff,80)

		if self.cubic_corr:
			plt.figure(figsize=(3*fig_width,2*fig_width))
			plt.subplot(221)
		else:
			plt.figure(figsize=(3*fig_width,1*fig_width))
			plt.subplot(121)
		
		if self.name is not None:
			plt.suptitle(self.name)

		b = int(np.nanmax(diff) - np.nanmin(diff) /(2*iqr(diff)*len(diff)**(-1/3)))
		if b > 10:
			b = 10
		plt.hist(diff,alpha=0.5,bins=b);

		plt.axvline(med,ls='--',color='k')
		plt.axvline(low,ls=':',color='k')
		plt.axvline(high,ls=':',color='k')

		s = ('$'+str((np.round(med,0)))+'^{+' + 
			str(int(np.round(high-med,0)))+'}_{'+
			str(int(np.round(low-med,0)))+'}$')
		plt.annotate(s,(.75,.8),fontsize=10,xycoords='axes fraction')
		plt.xlabel(r'Cal$-$Comp (mmag)',fontsize=15)
		plt.ylabel('Occurrence',fontsize=15)

		if self.cubic_corr:
			plt.subplot(222)
		else:
			plt.subplot(122)
		plt.plot(x[self.mask],diff,'.')
		
		if self.cubic_corr:
			xx = np.arange(min(x[self.mask]),max(x[self.mask]),0.01)
			plt.plot(xx,self.cubic_correction(x=xx)*1e3,label='Cubic correction')
		plt.axhline(med,ls='--',color='k')
		plt.axhline(low,ls=':',color='k')
		plt.axhline(high,ls=':',color='k')
		if self.cubic_corr:
			plt.legend(loc=1)
		if gi:
			plt.xlabel(r'$g-i$ (mag)',fontsize=15)
		else:
			plt.xlabel(r'$g-r$ (mag)',fontsize=15)
		plt.ylabel(r'Cal$-$Comp (mmag)',fontsize=15)


		if self.cubic_corr:
			diff = diff - self.cubic_correction(x=x)[self.mask] * 1e3
			med = np.percentile(diff,50)
			low = np.percentile(diff,16)
			high = np.percentile(diff,80)
			plt.subplot(223)
			b = int(np.nanmax(diff) - np.nanmin(diff) /(2*iqr(diff)*len(diff)**(-1/3)))
			if b > 10:
				b = 10
			plt.hist(diff,alpha=0.5,bins=b);

			plt.axvline(med,ls='--',color='k')
			plt.axvline(low,ls=':',color='k')
			plt.axvline(high,ls=':',color='k')

			s = ('$'+str((np.round(med,0)))+'^{+' + 
				str(int(np.round(high-med,0)))+'}_{'+
				str(int(np.round(low-med,0)))+'}$')
			plt.annotate(s,(.75,.8),fontsize=10,xycoords='axes fraction')
			plt.xlabel(r'Cal$-$Comp (mmag)',fontsize=15)
			plt.ylabel('Occurrence',fontsize=15)

			plt.subplot(224)
			plt.plot(x[self.mask],diff,'.')
			
			plt.axhline(med,ls='--',color='k')
			plt.axhline(low,ls=':',color='k')
			plt.axhline(high,ls=':',color='k')
			if gi:
				plt.xlabel(r'$g-i$ (mag)',fontsize=15)
			else:
				plt.xlabel(r'$g-r$ (mag)',fontsize=15)
			plt.ylabel(r'Cal$-$Comp (mmag)',fontsize=15)

		plt.tight_layout()

		if self.savename is not None:
			plt.savefig(self.savename+'_residuals.pdf')

	def R_vector(self,x=None): 
		"""
		Extinction vector coefficient vector (nothing is constant!).
		"""
		coeff = self.R_coeff
		return coeff[0] + coeff[1]*x #+ c3*x**2

	def _minimize_R_vector(self,coeff,x,y):
		"""
		Minimizing function for the extinction vector
		"""
		self.R_coeff = coeff
		fit = self.R_vector(x=x)
		return np.nansum(abs(fit-y))

	def calculate_R(self,plot=False):
		"""
		Calculate the coefficient for the extinction vector of the selected band.

		------
		Inputs
		------
		plot : `bool`
			Switch for plotting the diagnostic plot

		-------
		Returns
		-------
		self.R_coeff : `array`
			Array containing the two coefficients of the R vector.

		"""
		if self.mags is None:
			self.syn_calspec_mags()
		ebv = 0.1
		m_e = self.syn_calspec_mags(ebv=ebv)
		ext = (m_e - self.mags) / ebv


		gr = self.ps1_mags['g'] - self.ps1_mags['r']
		if self.gr_lims is None:
			ind = (gr < 1) #& (gr > -.2)
		else:
			ind = (gr > self.gr_lims[0]) & (gr < self.gr_lims[1])
		x = deepcopy(gr)
		x = x[ind]
		y = ext[ind]

		self.R_coeff = minimize(self._minimize_R_vector, [0,0], args=(x, y)).x
		fit = self.R_vector(x=x)
		clip = ~sigma_clip(y-fit,3,maxiters=10).mask
		self.R_coeff = minimize(self._minimize_R_vector, [0,0], 
								args=(x[clip], y[clip])).x

		if plot:
			plt.figure(figsize=(1.5*fig_width,1*fig_width))
			plt.plot(x[clip],y[clip],'.')
			plt.plot(x,fit,alpha=.5)
			plt.xlabel('$(g-r)_{int}$',fontsize=15)
			plt.ylabel('$R$',fontsize=15)
			
			s = r'$R=%(v2)s %(v1)s(g-r)_{int}$' % {'v1':str(np.round(self.R_coeff[1],3)),'v2':str(np.round(self.R_coeff[0],3))}
			plt.text(.04,.05,s,transform=plt.gca().transAxes,fontsize=12)
			#plt.title(s,fontsize=12)
			#plt.text(.6,.8,bb[i-1],transform=plt.gca().transAxes,fontsize=15)
			plt.tight_layout()

			if self.savename is not None:
				plt.savefig(self.savename + '_r_vector.pdf')


	def get_extinctions(self,mags):
		"""
		Get the E(B-V) extinction via Stellar Locus Regression.
		Loops through all sources calculating extinctions for sources 
		that are seperated by > 0.2 degrees.

		------
		Inputs
		------
		mags : `dict`
			Dictionary containing the PS1 magnitudes of the stars to calibrate

		-------
		Returns
		-------
		ebv : `array`
			Array containing the estimated E(B-V) extinction for the sources
		"""
		ebv = np.zeros(len(mags['g'])) * np.nan

		while np.isnan(ebv).any():
			i = np.where(np.isnan(ebv))[0][0]
			cal_stars = get_ps1_region(mags['ra'][i], mags['dec'][i],size=.2*60**2)
			
			e, dat = Tonry_reduce(cal_stars)

			dist = np.sqrt((mags['ra'] - mags['ra'][i])**2 + (mags['dec'] - mags['dec'][i])**2)
			ind = dist < .2*60**2
			ebv[ind] = e
		return ebv


	def estimate_mag(self,mags=None,ra=None,dec=None,correction=True,extinction=True,
					 gr_lims = None,size=3,catalog='vizier'):
		"""
		Calculate the expected composite magnitude for all sources provided.
		Either a table with the correct formatting or ra, and dec in deg can 
		be provided. 

		------
		Inputs
		------
		mags : pandas dataframe
			dataframe containing the PS1 magnitudes of the sources to make 
			a composite magnitude from.

		ra : `list` or float`
			RA positions for all objects of interest
		dec : `list` or float`
			Dec positions for all objects of interest

		-------
		Returns
		-------
		comp : `array`
			Composite magnitudes created for the targets of interest.
		"""
		print(cas_id)
		if (ra is not None) & (dec is not None):
			if catalog.lower() == 'vizier':
				mags = get_ps1(ra, dec, size)
			elif catalog.lower == 'casjobs':
				if (cas_id is not None) & (cas_pwd is not None):
					print('Using CASJobs to access PS1 DR2')
					mags = ps1_casjobs(ra, dec, size)
				else:	
					print('No CASJobs credentials saved, so using Vizier to access PS1 DR1')
					mags = get_ps1(ra, dec, size)
		
		if mags is None:
			m = 'No sources provided! Either give an ra-dec list, or a table with the appropriate format'
			raise ValueError(m)

		gr = mags['g'] - mags['r']
		mag2 = deepcopy(mags)
		
		if gr_lims is not None:
			ind = (gr > gr_lims[0]) & (gr < gr_lims[1])
			keys = list(mags.keys())
			mag2={}
			for key in keys:
				mag2[key] = mags[key][ind]
			gr = gr[ind]
		elif self.gr_lims is not None:
			ind = (gr > self.gr_lims[0]) & (gr < self.gr_lims[1])
			keys = list(mags.keys())
			mag2={}
			for key in keys:
				mag2[key] = mags[key][ind]
			gr = gr[ind]

		else:
			ind = np.isfinite(gr)
		
		if extinction:
			if self.R_coeff is None:
				self.calculate_R()
			ebv = self.get_extinctions(mag2)
		else:
			ebv = np.zeros(len(mag2['g']))


		comp = self.make_composite(mags = mag2,ext=ebv)
		if correction:
			comp -= self.cubic_correction(x=gr)
		
		final = np.zeros(len(mags['g'])) * np.nan
		final[ind] = comp
		return final


	def print_R(self):
		"""
		Print the R function in a nice way.
		"""
		if self.R_coeff is None:
			print('R coefficients have not been derived.')
			return
		else:
			from IPython.display import display, Math
			if self.name is None:
				eqn = r'$R=%(v2)s %(v1)s(g-r)_{int}$' % {'v1':str(np.round(self.R_coeff[1],3)),'v2':str(np.round(self.R_coeff[0],3))}
			else:
				eqn = r'$R_%(name)=%(v2)s %(v1)s(g-r)_{int}$' % {'name':self.name,'v1':str(np.round(self.R_coeff[1],3)),'v2':str(np.round(self.R_coeff[0],3))}
			display(Math(eqn))

	def ascii_R(self):
		"""
		Return the R coefficient function in ascii.
		"""
		if self.R_coeff is None:
			print('R coefficients have not been derived.')
			return
		else:
			eqn = 'R=%(v2)s %(v1)s(g-r)_(int)' % {'v1':str(np.round(self.R_coeff[1],3)),'v2':str(np.round(self.R_coeff[0],3))}
			
			return eqn

