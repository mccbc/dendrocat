from astropy.table import Table, MaskedColumn, Column
import astropy.units as u
from astropy import coordinates
from astropy.nddata.utils import Cutout2D
import numpy as np
from func import mask, rms
import regions
import matplotlib.gridspec as gs
import matplotlib.pyplot as plt

class SourceCatalog():
    """
    Contains an astropy.Table object, image attributes, and methods 
    for source rejection, matching, and flux measurement. 
    """

    def __init__(self, catalog=None, imageobj=None, masked=None):
                      
        if imageobj: 
            self.__dict__.update(imageobj.__dict__)
        self.catalog = Table(catalog, masked=masked)
        
    
    def _make_cutouts(self, sidelength, imageobj=None, catalog=None, 
                      save=True):
        """
        Make a cutout_data of cutout regions around all source centers in the 
        catalog.
        
        Parameters
        ----------
        sidelength : float
            Side length of the square (in degrees) to cut out of the image for 
            each source.
        imageobj : rsprocess.image.Image object, optional
            Image object from which to make the cutouts. If unspecified, image 
            information from instance attributes will be used.
        catalog : astropy.table.Table object, optional
            Source catalog to use for cutout coordinates. If unspecified, 
            catalog stored in instance attributes will be used.
        save : bool, optional
            If enabled, the cutouts and cutout data will both be saved as 
            instance attributes. Default is True.
            
        Returns
        ----------
        List of astropy.nddata.utils.Cutout2D objects, list of cutout data
            
        """
    
        if imageobj:
            beam = imageobj.beam
            wcs = imageobj.wcs
            pixel_scale = imageobj.pixel_scale
            data = imageobj.data
        else:
            beam = self.beam
            wcs = self.wcs
            pixel_scale = self.pixel_scale
            data = self.data
            
        if not catalog:
            catalog = self.catalog
        
        cutouts = []
        cutout_data = []
        
        for i in range(len(catalog)):
            x_cen = catalog['x_cen'][i] * u.deg
            y_cen = catalog['y_cen'][i] * u.deg
            
            position = coordinates.SkyCoord(x_cen, y_cen, frame='icrs',
                                            unit=(u.deg, u.deg))
            pixel_position = np.array(position.to_pixel(wcs))
            
            cutout = Cutout2D(data, position, sidelength, wcs, mode='partial')
            cutouts.append(cutout)
            cutout_data.append(cutout.data)
            
        cutouts = np.array(cutouts)
        cutout_data = np.array(cutout_data)
        
        if save:
            self._cutouts = cutouts
            self._cutout_data = cutout_data
        # NOTE: If 'sort' is called, the catalog's attributes also need to be
        # sorted accordingly. Might be tricky.
        
        return cutouts, cutout_data
    
    
    def save_ds9_regions(self, outfile):
        with open(outfile, 'w') as fh:
            fh.write("icrs\n")
            for row in cat:
                fh.write("ellipse({x_cen}, {y_cen}, {major_fwhm}, " \
                         "{minor_fwhm}, {position_angle}) # text={{{_idx}}}\n"
                         .format(**dict(zip(row.colnames, row))))
    
    
    def get_pixels_annulus(self, padding=None, width=None, save=True):
        """
        Return a list of pixel arrays, each of which contains the pixels in
        an annulus of constant width and variable radius depending on the 
        major fwhm of the source.
        
        Parameters
        ----------
        padding : astropy.units.deg, optional
            The additional spacing between the major fwhm of the source and
            the inner radius of the annulus.
        width : astropy.units.deg, optional
            The width of the annulus, in degrees.
        save : bool, optional
            If enabled, the pixel arrays and masks will both be saved as 
            instance attributes. Default is True.
            
        Returns
        ----------
        List of pixel arrays
        """
        
        if not padding:
            padding = self.default_annulus_padding
        
        if not width:
            width = self.default_annulus_width

        size = 2.2*(np.max(self.catalog['major_fwhm'])*u.deg 
                    + padding 
                    + width)
        cutouts, cutout_data = self._make_cutouts(size)
        
        pix_arrays = []
        masks = []
        
        for i in range(len(cutouts)):
            center = regions.PixCoord(cutouts[i].center_cutout[0], 
                                      cutouts[i].center_cutout[1])
            
            inner_r = self.catalog[i]['major_fwhm']*u.deg + padding
            outer_r = inner_r + width
            
            innerann_reg = regions.CirclePixelRegion(center, 
                                                     inner_r/self.pixel_scale)
            outerann_reg = regions.CirclePixelRegion(center, 
                                                     outer_r/self.pixel_scale)
            
            annulus_mask = (mask(outerann_reg, cutouts[i]) 
                            - mask(innerann_reg, cutouts[i]))
            
            pix_arrays.append(cutouts[i].data[annulus_mask.astype('bool')])
            masks.append(annulus_mask)
        
        if save:
            self.pixels_in_annulus = pix_arrays
            self.mask_annulus = masks
        
        return pix_arrays


    def get_pixels_ellipse(self, save=True):
        """
        Return a list of pixel arrays, each of which contains the pixels in
        the source ellipses.
        
        Parameters
        ----------
        save : bool, optional
            If enabled, the pixel arrays and masks will both be saved as 
            instance attributes. Default is True.
            
        Returns
        ----------
        List of pixel arrays
        """
        cutouts = self._cutouts
        # Currently, get_pixels_annulus needs to be run first to set the size
        # of the cutouts and save them as an instance attribute. Not ideal.
        
        pix_arrays = []
        masks = []
        
        for i in range(len(cutouts)):
            center = regions.PixCoord(cutouts[i].center_cutout[0], 
                                      cutouts[i].center_cutout[1])
            
            pix_major = self.catalog[i]['major_fwhm']*u.deg / self.pixel_scale
            pix_minor = self.catalog[i]['minor_fwhm']*u.deg / self.pixel_scale
            pa = self.catalog[i]['position_angle']*u.deg
            
            radius = self.catalog[i]['major_fwhm'] * u.deg
            reg = regions.EllipsePixelRegion(center, pix_major, pix_minor, 
                                             angle=pa)                 
            ellipse_mask = mask(reg, cutouts[i])
            pix_arrays.append(cutouts[i].data[ellipse_mask.astype('bool')])
            masks.append(ellipse_mask)
        
        if save:
            self.pixels_in_ellipse = pix_arrays
            self.mask_ellipse = masks
        
        return pix_arrays
        
    
    def get_snr(self, pixels_in_source=None, pixels_in_background=None, 
                peak=True, save=True):
        
        """
        Return the SNR of all sources in the catalog.
        
        Parameters
        ----------
        peak : bool, optional
            Use peak flux of source pixels as 'signal'. Default is True.
        save : bool, optional
            If enabled, the snr will be saved as a column in the source catalog
            and as an instance attribute. Default is True.
        """
        
        if not pixels_in_background:
            try:
                pixels_in_background = self.pixels_in_annulus
            except:
                pixels_in_background = self.get_pixels_annulus(1e-5*u.deg,
                                                               1e-5*u.deg)
                                                               
        if not pixels_in_source:
            try:
                pixels_in_source = self.pixels_in_ellipse
            except AttributeError:
                pixels_in_source = self.get_pixels_ellipse()
        
        snr_vals = []
        for i in range(len(self.catalog)):
            snr = np.max(pixels_in_source[i])/rms(pixels_in_background[i])
            snr_vals.append(snr)
            
        if save:
            self.snr = np.array(snr_vals)
            self.catalog.add_column(Column(snr_vals), name='snr_'+self.freq_id)
            
        return np.array(snr_vals)
    
    
    def plot_grid(self, cutout_data=None, masks=None, snr_vals=None,
                  skip=False):
        """
        Plot a grid of sources with aperture mask overlays. Rejected sources
        are shown in gray.
        
        Parameters
        ----------
        Documentation needed
        """
        
        if not snr_vals:
            try:
                snr_vals = self.snr
            except AttributeError:
                snr_vals = self.get_snr()
                
        if not cutout_data:
            cutout_data = self._cutout_data
        
        if not masks:
            masks = np.array(list(zip(self.mask_ellipse, self.mask_annulus)))
            
        names = np.array(self.catalog['_idx'])
        rejected = np.array(self.catalog['rejected'])
        
        if skip:
            accepted_indices = np.where(self.catalog['rejected'] == 0)[0]
            snr_vals = snr_vals[accepted_indices]
            cutout_data = cutout_data[accepted_indices]
            masks = masks[accepted_indices]
            names = names[accepted_indices]
            rejected = rejected[accepted_indices]
        
        n_images = len(cutout_data)
        xplots = int(np.around(np.sqrt(n_images)))
        yplots = xplots + 1
        gs1 = gs.GridSpec(yplots, xplots, wspace=0.0, hspace=0.0)
        plt.figure(figsize=(9.5, 10))
        
        for i in range(n_images):
            image = cutout_data[i]
            plt.subplot(gs1[i])
            
            if rejected[i] == 1:
                plt.imshow(image, origin='lower', cmap='gray')
            else:
                plt.imshow(image, origin='lower')

            for j in range(len(masks[i])):
                plt.imshow(masks[i][j], origin='lower', cmap='gray', 
                           alpha=0.25)
                
            plt.text(0, 0, '{}  SN {:.1f}'.format(names[i], snr_vals[i]), 
                     fontsize=7, color='w')
            plt.xticks([])
            plt.yticks([])
        
        plt.tight_layout()
        plt.show()
    
    
    def autoreject(self, threshold=6.):
        """
        Reject noisy detections.
        
        Parameters
        ----------
        threshold : float, optional
            The signal-to-noise threshold below which sources are rejected
        """
            
        if not threshold:
            threshold = self.default_threshold
        
        try:
            snrs = self.snr
        except:
            snrs = self.get_snr()
            
        try:
            self.catalog['rejected'] = np.zeros(len(self.catalog), dtype=int)
        except KeyError:
            self.catalog.add_column(Column(np.zeros(len(self.catalog))), 
                                    name='rejected')
    
        for i in range(len(self.catalog)):
            if snrs[i] <= threshold:
                self.catalog['rejected'][i] = 1
                    

    def reject(self, rejected_list):
    
        for idx in rejected_list:
            self.catalog[np.where(catalog['_idx'] == idx)]['rejected'] = 1
            
            
    def accept(self, accepted_list):
        
        for idx in accepted_list:
            self.catalog[np.where(catalog['_idx'] == idx)]['rejected'] = 0


if __name__ == '__main__':
    from astropy.io import fits
    from image import Image
    
    filename = '/lustre/aoc/students/bmcclell/w51/W51e2_cont_briggsSC_tclean.image.fits.gz'
    f = fits.open(filename)
    i = Image(f)
    i.to_dendrogram()
    c = i.to_cat()
