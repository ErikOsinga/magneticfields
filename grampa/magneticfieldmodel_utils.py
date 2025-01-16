import numpy as np
import matplotlib.pyplot as plt
import pyfftw

from scipy import stats
from astropy.convolution import convolve, Gaussian2DKernel


import logging
logger = logging.getLogger(__name__)

def beta_model(r, ne0=0.0031, r_c=341, beta=0.77):
    """
    Typical beta model for cluster electron density profile

    default is Osinga+24 parameters for Abell 2256, from Remi Adam
    
    r    -- float or array -- radius in kpc [kpc] 
    ne0  -- float  -- central electron density in cm^-3
    r_c  -- float  -- core radius in kpc
    beta -- float  -- beta parameter
    
    """    
    return ne0 * (1+(r/r_c)**2)**(-3*beta/2)

def RM_integration(n_e,B_field,pixsize,axis):
    """
    Calculate Rotation measure by integrating over a certain axis
    (Riemann sum)
    
    Integrating over one pixel is integration over pixsize*1000 parsecs.
    """
    return 0.81*pixsize*1e3*np.sum(n_e*B_field[:,:,:,axis],axis=axis)

def RM_halfway(n_e,B_field,pixsize,axis):
    """
    Calculate Rotation measure by 'integrating' over a certain axis.
    Now only integrate over half the axis
    
    
    Integrating over one pixel is integration over pixsize*1000 parsecs.
    """
    N = len(n_e)
    # For keeping track over which axis we want to do the integrating
    if axis == 0:
        N0 = N//2
        N1 = N
        N2 = N
    elif axis == 1:
        N0 = N 
        N1 = N//2
        N2 = N
    elif axis == 2:
        N0 = N 
        N1 = N
        N2 = N//2
    else:
        raise ValueError("Axis not implemented")
        
    return 0.81*pixsize*1e3*np.sum(n_e[:N0,:N1,:N2]*B_field[:N0,:N1,:N2,axis],axis=axis)

def fftIndgen(n):
    a = list(range(0, n//2+1))
    b = list(range(1, n//2))
    b.reverse()
    b = [-i for i in b]
    return a + b

def kvector(N, ndim, pixsize, ftype):
    """
    Generate ( N(xN)xN//2+1) ndim matrix of k vector values
    Since we need to do the IFFT of k cross A
    we also need this kvector array
    """
    dk = ftype(2*np.pi/N/pixsize)
    # Frequency terms, positive frequencies up unto half of the array
    # Nyquist frequency at position N//2, then negative frequencies up to -1
    ks = np.array(np.concatenate([np.arange(0, N//2+1),np.arange(-N//2+1,0)]),dtype=ftype)
    ks *= dk
                
    # My implementation of the c_field has a different definition
    # for the x axis than numpy, thus swap y and x from np.meshgrid
    if ndim == 2:
        # every particle has a 2D position
        kvector = np.zeros((N,N//2+1,ndim),dtype=ftype)
        # simply replaces more of the same for loops
        ky, kx = np.meshgrid(ks,ks) # construct a grid
        kvector[:,:,0] = kx[:,:N//2+1]
        kvector[:,:,1] = ky[:,:N//2+1]
    elif ndim == 3:
        # every particle has a 3D position. Only need half of the cube?
        kvector = np.zeros((N,N,N//2+1,ndim),dtype=ftype)
        ky, kx, kz = np.meshgrid(ks,ks,ks)
        kvector[:,:,:,0] = kx[:,:,:N//2+1]
        kvector[:,:,:,1] = ky[:,:,:N//2+1]
        kvector[:,:,:,2] = kz[:,:,:N//2+1]

    return kvector

def kvector_lengthonly(N, ftype):
    """
    Get the normalised length of the fft indices in 3D

    Only half of the cube is generated. Other half is redundant
    """

    kxkykz = np.zeros((N,N,N//2+1,3),dtype=ftype)
    indices = fftIndgen(N)
    ky, kx, kz = np.meshgrid(indices,indices,indices)
    kxkykz[:,:,:,0] = kx[:,:,:N//2+1]
    kxkykz[:,:,:,1] = ky[:,:,:N//2+1]
    kxkykz[:,:,:,2] = kz[:,:,:N//2+1]
    # Power spectrum only depends on the length
    k_length = np.linalg.norm(kxkykz,axis=-1)

    return k_length

def kvector_lengthonly_2D(N, ftype):
    """
    Get the normalised length of the fft indices in 2D (e.g. for the RM field)

    Only half of the cube is generated. Other half is redundant for a real field
    """

    kxky = np.zeros((N,N//2+1,2),dtype=ftype)
    indices = fftIndgen(N)
    ky, kx = np.meshgrid(indices,indices)
    kxky[:,:,0] = kx[:,:N//2+1] # only half of the 3rd axis
    kxky[:,:,1] = ky[:,:N//2+1] # only half of the 3rd axis
    # Power spectrum only depends on the length
    k_length = np.linalg.norm(kxky,axis=-1)

    return k_length

def xvector(N, ndim, pixsize, ftype, subcube=False):
    """
    Generate NxN(xN)xndim matrix of x vector values
    
    This is simply a vector that goes from -31 to 32 because the real space has
    no origin before I set the origin. So xvec[31,N//2-1,N//2-1] is the origin

    if subcube -- Because its symmetric, only need a small part of cube (only positive quadrant)
    """
    xs = np.arange(-N//2+1,N//2+1,dtype=ftype)*pixsize # aranged vector of x positions
                
    # My implementation of the field has a different definition
    # for the x axis than numpy, thus swap y and x from np.meshgrid
    if ndim == 2:
        # every particle has a 2D position
        xvector = np.zeros((N,N,ndim),dtype=ftype)
        # simply replaces more of the same for loops
        y, x = np.meshgrid(xs,xs) # construct a grid
        xvector[:,:,0] = x
        xvector[:,:,1] = y
    elif ndim == 3:

        if not subcube:
            # every particle has a 3D position
            xvector = np.zeros((N,N,N,ndim),dtype=ftype)
            y, x, z = np.meshgrid(xs,xs,xs)
            xvector[:,:,:,0] = x
            xvector[:,:,:,1] = y
            xvector[:,:,:,2] = z

        else:
            # Use radial symmetry to only get  1/N^ndim of the cube
            # Only implemented for ndim==3, so 1/8th of the cube
            xvector = np.zeros((N//2+1,N//2+1,N//2+1,ndim),dtype=ftype)
            y, x, z = np.meshgrid(xs,xs,xs)
            xvector[:,:,:,0] = x[N//2-1:,N//2-1:,N//2-1:] # start from 0, omit negative part
            xvector[:,:,:,1] = y[N//2-1:,N//2-1:,N//2-1:]
            xvector[:,:,:,2] = z[N//2-1:,N//2-1:,N//2-1:]

            # e.g. for N=6 
            # x now contains [0,1,2,3]
            # instead of [-2,1,0,1,2,3]

    return xvector

def xvector_length(N, ndim, pixsize, ftype, subcube=False):
    """
    Call the function above and then compute only the length
    """
    # Now runs from -31 to +32 which is 64 values. Or 0 to +32 when subcube=True
    xvec = xvector(N, ndim, pixsize, ftype, subcube)
    # The norm of the position vector
    xvec_length = np.linalg.norm(xvec,axis=-1)
    return xvec_length

def cube_from_subcube(subcube, cubeshape, ftype):
    """
    Using radial symmetry to fill in the rest of the cube 

    if cubeshape is N then we assume (N,N,N), then subcube should be of shape (N//2+1,N//2+1,N//2+1)
    assuming that the symmetry (0) axis is the first index and the final index doesnt have to be flipped
    (i.e. also assuming that N == even)
    """
    N = cubeshape
    cube = np.zeros((N,N,N),dtype=ftype)

    ### Fill 8 subcubes by flipping across the negative axis
    # all 'negative' axis directions
    cube[:N//2-1,:N//2-1,:N//2-1] = np.flip(subcube[1:N//2,1:N//2,1:N//2])
    # 'positive' x direction, negative others
    cube[N//2-1:,:N//2-1,:N//2-1] = np.flip(subcube[0:,1:N//2,1:N//2],axis=(1,2))
    # 'positive' y direction, negative others
    cube[:N//2-1,N//2-1:,:N//2-1] = np.flip(subcube[1:N//2,0:,1:N//2],axis=(0,2))
    # 'positive' z direction, negative others
    cube[:N//2-1,:N//2-1,N//2-1:] = np.flip(subcube[1:N//2,1:N//2,0:],axis=(0,1))
    # positive x, positive y, negative z
    cube[N//2-1:,N//2-1:,:N//2-1] = np.flip(subcube[0:,0:,1:N//2],axis=(2))
    # positive x, negative y, positive z
    cube[N//2-1:,:N//2-1,N//2-1:] = np.flip(subcube[0:,1:N//2,0:],axis=(1))
    # negative x, positive y, positive z
    cube[:N//2-1,N//2-1:,N//2-1:] = np.flip(subcube[1:N//2,0:,0:],axis=(0))
    # all positive
    cube[N//2-1:,N//2-1:,N//2-1:] = subcube[0:,0:,0:]

    return cube

def model_xi(k, xi, N, lambdamax, pixsize, indices=True):
    """
    Evaluate a given powerlaw Pk ~ k^-xi 
    With possible maximum spatial scale Lambda_max given in kpc

    The maximum scale is defined as the magnetic field reversal scale,
    see footnote in Murgia+2004. In this way, Lambda = 0.5* 2*np.pi/k 
    Thus the smallest possible k mode (k=1) always corresponds to Lambda=(N*pixsize)/2
    e.g., Lambda_max = 512 kpc for N=1024 and p=1
    Thus the next k mode (k=2) corresponds to 256 kpc and k=2 to 128 kpc etc..

    indices -- boolean -- whether 'k' (the 'k-modes') are given as indices or as values
    """

    if lambdamax is None: # scale invariant. Easy.
        return k**-xi
    else:
        result = k**-xi

        # The wave mode that corresponds to the given Lambda_max in kpc
        #### Following Murgia definition that Lambda is the half-wavelength = 0.5*(2pi/k)
        kmax = np.pi/lambdamax  

        # The index of the wave mode that corresponds to the given Lambda max in kpc
        k_index_max = (N*pixsize/2) / lambdamax

        # Because the Gaussian_random_field function uses indices, indices
        # are given to this function, so we should mask on index length
        if indices:
            result[k<k_index_max] = 0

        else: # Mask all k modes that are smaller than kmax, corresponds to larger than Lambda_max
            result[k<kmax] = 0

        return result

def magnetic_field_crossproduct(kvec, field, N, ctype):
    """
    Do the cross product of i*k and A(k), keeping in mind complex conjugate symmetries.

    Way faster than the old method that used loops, but only tested for even amount of N.
    And a bit more complex/obscure in the implementation. But gives exactly
    the same results as the loop method. 

    In the case that N=even (say 64)
    the complex conjugate symmetry is destroyed by the cross product
    because for index 32, the value of k_vec = pi but for -32 it's also pi. 
    But it should be -pi to keep complex conjugate symmetry. 
    # But -pi is pi in the Fourier plane because it flips there. 
    
    So it goes wrong in (64*63 - 64) cases, because that's how many points you can
    find in 3 dimensions where the coordinate on one axis is 32. 
    
    We can fix this by just doing the cross product in the half of the Fourier cube
    and taking the complex conjugate, just like how we determined the Field. 
    """
    
    fourier_B_field = np.zeros((N,N,N//2+1,3),dtype=ctype)
    # The fourier frequencies are different for (un)even N
    Neven = N%2 # add one to loops if N is uneven.
    # ONLY TESTED FOR EVEN N
                
    # all kz modes, all ky modes, half of the fourier cube, thus z=1 to N//2 (because z=0 and z//2 are special)
    fourier_B_field[:,:,1:N//2+Neven] = np.cross(1j*kvec[:,:,1:N//2+Neven], field[:,:,1:N//2+Neven],axis=-1)
                
    if Neven == 0:
        # We have an even amount of N, so do not forget the j = N//2
        # plane. It's conjugate symmetry is special because -N//2 = N//2
        # Similarly, the j=0 plane is also conjugate symmetric in the y-axis
        
        
        # We can generate half of this plane, since it's symmetric in the y axis
        # and then impose symmetry on the plane itself.
        # This is the equivalent to generating a 2D plane density field
                
        # The z=N//2 plane
        z = N//2
        # all kz modes, but start on kx at i=1 and end at N//2, because those two axes are special
        fourier_B_field[1:N//2,:,z] = np.cross(1j*kvec[1:N//2,:,z], field[1:N//2,:,z],axis=-1)      
        # The other half of the plane, complex conjugate symmetric (Hermitian symmetric)
        # Careful to also np.roll(1) in the np.flip, since otherwise we adjust the z=0 axis
        # e.g. for N=6 kx=[0, 1, 2, 3,-2,-1], so np.flip gives [-1,-2,3,2,1,0] and np.roll(flip) gives
        #                 [0,-1,-2, 3, 2, 1] as we want.
        # So for a 2D array, in this case we roll over the axis that is length N (axis=1 in this case)
        fourier_B_field[N//2+1:,:,z] = np.conj(np.roll(np.flip(fourier_B_field[1:N//2,:,z],axis=(0,1)),1,axis=1))    

        # The z=0 plane
        z = 0
        # all kz modes, but start on kx at i=1 and end at N//2, because those two axes are special
        fourier_B_field[1:N//2,:,z] = np.cross(1j*kvec[1:N//2,:,z], field[1:N//2,:,z],axis=-1)      
        # The other half of the plane, complex conjugate symmetric (Hermitian symmetric)
        fourier_B_field[N//2+1:,:,z] = np.conj(np.roll(np.flip(fourier_B_field[1:N//2,:,z],axis=(0,1)),1,axis=1))    
        

        # Don't forget the x=N//2 column, which we can generate half for
        # ky modes up to half (N//2)
        fourier_B_field[N//2,:N//2,N//2] = np.cross(1j*kvec[N//2,:N//2,N//2], field[N//2,:N//2,N//2],axis=-1)       
        # The other half is complex conjugate. Don't have to roll here, because only 1 axis
        fourier_B_field[N//2,N//2+1:,N//2] = np.conj(np.flip(fourier_B_field[N//2,1:N//2,N//2],axis=0)) 

        # And the x=0 column 
        fourier_B_field[0,:N//2,N//2] = np.cross(1j*kvec[0,N//2,:N//2], field[0,N//2,:N//2],axis=-1)       
        # The other half is complex conjugate
        fourier_B_field[0,N//2+1:,N//2] = np.conj(np.flip(fourier_B_field[0,1:N//2,N//2],axis=0)) 
        
        # same for when kz=0. Do the x=N//2 column
        fourier_B_field[N//2,:N//2,0] = np.cross(1j*kvec[N//2,:N//2,0], field[N//2,:N//2,0],axis=-1)       
        # The other half is complex conjugate
        fourier_B_field[N//2,N//2+1:,0] = np.conj(np.flip(fourier_B_field[N//2,1:N//2,0],axis=0)) 
        
        # And the kz=0, x=0 column, which we can also generate half for. 
        fourier_B_field[0,:N//2,0] = np.cross(1j*kvec[0,:N//2,0], field[0,:N//2,0],axis=-1)
        # The other half is complex conjugate
        fourier_B_field[0,N//2+1:,0] = np.conj(np.flip(fourier_B_field[0,1:N//2,0],axis=0)) 
                    
                
        # Now some numbers are their own complex conjugate.
        # i.e., they are real.
        fourier_B_field[0, 0, N//2] = fourier_B_field[0,0,N//2].real
        fourier_B_field[0, N//2, 0] = fourier_B_field[0,N//2,0].real
        fourier_B_field[N//2, 0, 0] = fourier_B_field[N//2, 0, 0].real
        
        fourier_B_field[0, N//2, N//2] = fourier_B_field[0,N//2,N//2].real
        fourier_B_field[N//2, N//2, 0] = fourier_B_field[N//2,N//2,0].real
        fourier_B_field[N//2, 0, N//2] = fourier_B_field[N//2, 0, N//2].real
        
        fourier_B_field[N//2, N//2, N//2] = fourier_B_field[N//2, N//2, N//2].real
        
    # Don't forget that the [0,0] component of the field has to be 0
    fourier_B_field[0,0,0] = 0 + 1j*0   
    
    # Now we don't have to generate the modes in the other half of the Fourier cube
    # because it's a redundant part, so we can just use irfftn    
    
    return fourier_B_field

def gaussian_random_field3D(N, Pk, ftype, k_length=None):
    """
    Adapted from http://andrewwalker.github.io/statefultransitions/post/gaussian-fields/

    Is actually nicely explained by https://garrettgoon.com/gaussian-fields/
    """
    ## Use fft for real values. Try to avoid copying to save memory
    run_fftw = pyfftw.builders.rfftn(np.random.normal(size = (N,N,N)).astype(ftype)
        , auto_contiguous=False, auto_align_input=False, avoid_copy=True,threads=48)
    noise = run_fftw()

    if k_length is None:
        print ("WARNING. When calling gaussian_random_field multiple times. Recommended to give k_length")
        k_length = kvector_lengthonly(N, ftype)

    # amplitude = np.zeros((N,N,N))
    amplitude = np.sqrt(Pk(k_length))
    amplitude[0,0,0] = 0 # assume k=0 is on 0,0,0
    
    field = noise * amplitude
    return field

def calculate_vectorpotential(N, xi, Lambda_max, pixsize, ftype):
    """
    Calculate the vector potential A as a Gaussian random field

    Starting from Afield, Bfield or from scratch (if recompute=True)

    """
    # Set indices=True if we are computing it the fast way, with the gaussian_random_field3D function
    def Peff(k):
        return model_xi(k, xi, N, Lambda_max, pixsize, indices=True)

    logger.info("Generating random field for vector potential A.")
    # Every component of the 3D cube of A is a vector, since A is a vector field.
    # So A has shape (N,N,N,3)
    # So we just generate three randomfield cubes, and each cube will be one dimension of the vector.

    # Make field without the redundant Fourier components
    field = np.zeros((N,N,N//2+1,3),dtype=ftype) + 1j*np.zeros((N,N,N//2+1,3),dtype=ftype)

    # Get the normalised index length in 3D space
    k_length = kvector_lengthonly(N, ftype)

    logger.info("Random field x-dimension..")
    field[:,:,:,0] = gaussian_random_field3D(N, Peff, ftype, k_length)
    logger.info ("Random field y-dimension..")
    field[:,:,:,1] = gaussian_random_field3D(N, Peff, ftype, k_length)
    logger.info("Random field z-dimension..")
    field[:,:,:,2] = gaussian_random_field3D(N, Peff, ftype, k_length)
    
    return field

def normalise_Bfield_subcube(B_field, average_profile, B0, ne_3d_subcube, ne0, eta):
    """
    Subfunction of def normalise_Bfield(): compute without the need of expanding ne_3d to a full cube.

    B_field         -- Magnetic field at every point in the 3D space. shape (N,N,N,3)
                                                            i.e. ~100 GB for N=2048
    ne_3d_subcube   -- electron density subcube at every point in the 3D space. shape (N//2+1, N//2+1, N//2+1)
    ne0             -- electron density in the center of the cluster
    eta             -- Proportionality of B to n_e
    B0              -- Mean magnetic field in center
    """
    N = len(B_field)
    B_field_norm = np.zeros_like(B_field, dtype=np.float32)

    # Calculate normalized B_field for the subcube and replicate to full cube
    # All 'negative' axis directions
    B_field_norm[:N//2-1, :N//2-1, :N//2-1] = (
        B_field[:N//2-1, :N//2-1, :N//2-1]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[1:N//2, 1:N//2, 1:N//2] / ne0), eta)[..., None]
    )

    # 'Positive' x direction, negative others
    B_field_norm[N//2-1:, :N//2-1, :N//2-1] = (
        B_field[N//2-1:, :N//2-1, :N//2-1]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[0:, 1:N//2, 1:N//2] / ne0, axis=(1, 2)), eta)[..., None]
    )

    # 'Positive' y direction, negative others
    B_field_norm[:N//2-1, N//2-1:, :N//2-1] = (
        B_field[:N//2-1, N//2-1:, :N//2-1]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[1:N//2, 0:, 1:N//2] / ne0, axis=(0, 2)), eta)[..., None]
    )

    # 'Positive' z direction, negative others
    B_field_norm[:N//2-1, :N//2-1, N//2-1:] = (
        B_field[:N//2-1, :N//2-1, N//2-1:]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[1:N//2, 1:N//2, 0:] / ne0, axis=(0, 1)), eta)[..., None]
    )

    # Positive x, positive y, negative z
    B_field_norm[N//2-1:, N//2-1:, :N//2-1] = (
        B_field[N//2-1:, N//2-1:, :N//2-1]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[0:, 0:, 1:N//2] / ne0, axis=(2)), eta)[..., None]
    )

    # Positive x, negative y, positive z
    B_field_norm[N//2-1:, :N//2-1, N//2-1:] = (
        B_field[N//2-1:, :N//2-1, N//2-1:]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[0:, 1:N//2, 0:] / ne0, axis=(1)), eta)[..., None]
    )

    # Negative x, positive y, positive z
    B_field_norm[:N//2-1, N//2-1:, N//2-1:] = (
        B_field[:N//2-1, N//2-1:, N//2-1:]
        / average_profile
        * B0
        * np.power(np.flip(ne_3d_subcube[1:N//2, 0:, 0:] / ne0, axis=(0)), eta)[..., None]
    )

    # All positive
    B_field_norm[N//2-1:, N//2-1:, N//2-1:] = (
        B_field[N//2-1:, N//2-1:, N//2-1:]
        / average_profile
        * B0
        * np.power(ne_3d_subcube[0:, 0:, 0:] / ne0, eta)[..., None]
    )

    return B_field_norm

def normalise_Bfield(ne_3d, ne0, B_field, eta, B0, subcube=False):
    """
    Normalise the B field such that it follows the electron density profile

    ne_3d   -- electron density at every point in the 3D space. shape (N,N,N)
    ne0     -- electron density in the center of the cluster
    B_field -- Magnetic field at every point in the 3D space. shape (N,N,N,3)
    eta     -- Proportionality of B to n_e
    B0      -- Mean magnetic field in center
    subcube -- Because its symmetric, only need a small part of cube (only positive quadrant)

    """
    N = len(B_field)

    # Compute the radial profile of the central slice (symmetric, and should be flat)
    # Can simply use np.mean(B_field_amplitude) instead of radial profile
    B_field_amplitude = np.linalg.norm(B_field[:,:,N//2-1,:],axis=2) # (N,N)
    average_profile = np.mean(B_field_amplitude) # Bfield should have no radial dependence yet
                                                 # Just some random normalisation
    
    if subcube:
        # make the full cubes for the normalisation of the B field
        # Expand 1/8th of the cube to the full cube. 
        
        # ne_3d = cube_from_subcube(ne_3d, N)
        B_field_norm = normalise_Bfield_subcube(B_field, average_profile, B0, ne_3d, ne0, eta)

    else:
        # Normalise the B field to mean 1*B0 and then multiply by the normalised profile
        # B_field_norm = B_field/average_profile.reshape(N,N,N,1)* B0 * (np.power(ne_3d / ne0, eta)).reshape(N,N,N,1)
        B_field_norm = B_field/average_profile * B0 * (np.power(ne_3d / ne0, eta))[:,:,:,None]
        
    return B_field_norm, ne_3d

def radial_profile(data, center):
    """
    Calculate radial profile of array 'data', given the center 'center'
    """
    y, x = np.indices((data.shape))
    r = np.sqrt((x - center[0])**2 + (y - center[1])**2)
    r = r.astype(int)

    tbin = np.bincount(r.ravel(), data.ravel())
    nr = np.bincount(r.ravel())
    radialprofile = tbin / nr
    radius = np.arange(0,np.max(r)+1) # in pixels. This is how np.bincount bins.

    return radius, radialprofile 

def shell_averaged_power_spectrum(field, component='total', multiply_volume=False):
    """
    # Assuming the input cube 'field' is the Fourier field with dimensions e.g. (512, 512, 256, 3)
    # so it has to be conjugate symmetric. 
    # assuming it's not FFT shifted, so kx=0 is at index=0 instead of the centre
    """
    nx, ny, nz, _ = field.shape

    if component == 'total':
        # Compute the squared magnitude of the field strength (i.e. |Bx,By,Bz| )
        power_spectrum = np.linalg.norm(np.abs(field)**2, axis=-1)  # shape (512, 512, 256)
    elif component == 'x':
        # Compute the squared magnitude of the Bx component only
        power_spectrum = (np.abs(field[:,:,:,0])**2)  # shape (512, 512, 256)
    elif component == 'y':
        # Compute the squared magnitude of the By component only
        power_spectrum = (np.abs(field[:,:,:,1])**2)  # shape (512, 512, 256)        
    elif component == 'z':
        # Compute the squared magnitude of the Bz component only
        power_spectrum = (np.abs(field[:,:,:,2])**2)  # shape (512, 512, 256)        

    # Create the wavenumber grid (half of the cube)
    k_magnitude = kvector_lengthonly(nx, np.float32) # assumes nx=ny=nz

    # Flatten the k_magnitude and power_spectrum arrays
    k_magnitude = k_magnitude.ravel()
    power_spectrum = power_spectrum.ravel()

    # Define the bins for shell averaging
    k_max = nx//2+1 # only have good sampling in k up to nx//2
                    # although k_max is technically sqrt(Ndim)*nx//2
                    # then we would be sampling outside the image because the 'circle' is too large
    k_bins = np.arange(0.5, k_max + 1.5, 1.0)
    # Compute the corresponding k values for the shell-averaged power spectrum
    k_values = 0.5 * (k_bins[1:] + k_bins[:-1])

    # Bin the power spectrum values based on the wavenumber magnitude
    Abins, _, _ = stats.binned_statistic(k_magnitude, power_spectrum,
                                         statistic = "mean", # statistic = sum
                                         bins = k_bins)

    # Multiply by the volume to go from 3D to 1D power spectrum
    if multiply_volume:
        # if 2D field (see below)
        # Abins *= np.pi * (k_bins[1:]**2 - k_bins[:-1]**2)  # in 2D volume (area) is pi r^2
        
        # if 3D field (default)
        Abins *= 4. * np.pi / 3. * (k_bins[1:]**3 - k_bins[:-1]**3)  # in 3D volume is 4/3 pi r^3

    return k_values, Abins

def shell_averaged_power_spectrum2D(field, multiply_volume=False):
    """
    # Assuming the input cube 'field' is the Fourier field with dimensions e.g. (512, 256)
    # so it has to be conjugate symmetric. 
    # assuming it's not FFT shifted, so kx=0 is at index=0 instead of the centre
    """
    nx, ny = field.shape

    # Compute the squared magnitude 
    power_spectrum = np.abs(field)**2  # shape (512, 256)

    # Create the wavenumber grid (half of the cube)
    k_magnitude = kvector_lengthonly_2D(nx, np.float32) # assumes nx=ny=nz

    # Flatten the k_magnitude and power_spectrum arrays
    k_magnitude = k_magnitude.ravel()
    power_spectrum = power_spectrum.ravel()

    # Define the bins for shell averaging
    k_max = nx//2+1 # only have good sampling in k up to nx//2
                    # although k_max is technically sqrt(Ndim)*nx//2
                    # then we would be sampling outside the image because the 'circle' is too large
    k_bins = np.arange(0.5, k_max + 1.5, 1.0)
    # Compute the corresponding k values for the shell-averaged power spectrum
    k_values = 0.5 * (k_bins[1:] + k_bins[:-1])

    # Bin the power spectrum values based on the wavenumber magnitude
    Abins, _, _ = stats.binned_statistic(k_magnitude, power_spectrum,
                                         statistic = "mean", # statistic = sum
                                         bins = k_bins)

    # Multiply by the volume to go from 2D to 1D power spectrum
    if multiply_volume:
        # if 2D field
        Abins *= np.pi * (k_bins[1:]**2 - k_bins[:-1]**2)  # in 2D volume (area) is pi r^2
        
        # if 3D field (see function above)
        # Abins *= 4. * np.pi / 3. * (k_bins[1:]**3 - k_bins[:-1]**3)  # in 3D volume is 4/3 pi r^3

    return k_values, Abins

def plot_Bfield_amp_vs_radius(B_field_norm, pixsize, dens_model, B0):
    """
    Plots only made when --testing is enabled
    """
    N = len(B_field_norm)

    # Calculate the amplitude of the B field
    B_field_amplitude = np.linalg.norm(B_field_norm,axis=3)
    plt.imshow(B_field_amplitude[:,:,N//2])
    plt.title("Normalised B field amplitude, central slice")
    plt.colorbar()
    plt.show()

    # Plot the profile of the central slice
    all_r, profile = radial_profile(B_field_amplitude[:,:,N//2-1], center=[N//2-1,N//2-1])
    all_r *= np.int32(pixsize)
    fig, ax = plt.subplots(figsize=(8,8))
    plt.plot(all_r,profile,label='Magnetic field simulated',marker='o',markersize=2)
    # Compare with density profile
    density = dens_model(all_r)
    plt.plot(all_r,((density/density[0])**0.5)*B0,label='Density profile $^{0.5}$')
    plt.legend()
    plt.show()

def plot_B_field_powerspectrum(B_field_norm, xi, Lambda_max):
    """
    Plots only made when --testing is enabled
    """
    ## Use fft for real values. Try to avoid copying to save memory
    run_fftw = pyfftw.builders.rfftn(B_field_norm
        , auto_contiguous=False, auto_align_input=False, avoid_copy=True,threads=48, axes=(0,1,2))
    B_field_norm_fft = run_fftw()
    
    k_values, Pk_values = shell_averaged_power_spectrum(B_field_norm_fft, component='total', multiply_volume=False)
    plt.loglog(k_values, Pk_values, label='Data')
    # Compare with expectation
    amplitude = Pk_values[0]
    alpha = xi-2
    theoretical = amplitude*np.asarray(k_values,dtype='float')**-alpha * (k_values[0]**alpha)
    plt.plot(k_values, theoretical,label='Pk = %.e k**-%.1f'%(amplitude,alpha), ls='dashed')
    plt.xlabel('$k$')
    plt.ylabel("$P(k)$")
    plt.legend()
    plt.title(f"Power spectrum of normalised B-field. {Lambda_max=} kpc")
    plt.tight_layout()
    plt.show()

def plotRMimage(RMimage, pixsize, title=''):
    N = len(RMimage)

    extent = [-(N//2+1)*pixsize, (N//2)*pixsize, -(N//2+1)*pixsize, (N//2)*pixsize]

    plt.imshow(RMimage,extent=extent,origin='lower')
    cbar = plt.colorbar()
    cbar.set_label("RM [rad m$^{-2}$]")
    plt.xlabel('x [kpc]')
    plt.ylabel('y [kpc]')
    plt.title(title)
    plt.show()

def plot_RM_powerspectrum(RMimage, xi , Lambda_max, title=''):
    """
    Plots only made when --testing is enabled
    """
    ## Use fft for real values. Try to avoid copying to save memory
    run_fftw = pyfftw.builders.rfftn(RMimage
        , auto_contiguous=False, auto_align_input=False, avoid_copy=True,threads=48)
    RMimage_fft = run_fftw()
    k_values, Pk_values = shell_averaged_power_spectrum2D(RMimage_fft, multiply_volume=False)
    plt.loglog(k_values, Pk_values, label='Data')
    # Compare with expectation
    amplitude = Pk_values[0]
    alpha = xi-2
    # According to Murgia+2004
    theoretical = amplitude*np.asarray(k_values,dtype='float')**-alpha * (k_values[0]**alpha)
    plt.plot(k_values, theoretical,label='Pk = %.e k**-%.1f'%(amplitude,alpha), ls='dashed')
    # According to Seta+2022 for a constant electron density and magnetic field strength
    theoretical2 = amplitude*np.asarray(k_values,dtype='float')**(-alpha-1) * (k_values[0]**(alpha+1))
    plt.plot(k_values, theoretical2,label='Pk = %.e k**-%.1f'%(amplitude,alpha+1), ls='dashed')
    plt.xlabel('$k$')
    plt.ylabel("$P(k)$")
    plt.legend()
    plt.title(f"Power spectrum of RM image. {Lambda_max=}")
    plt.tight_layout()
    plt.show()

def convolve_with_beam(images, FWHM, pixsize=1.0):
    """
    Convolve the images with a (circular) Gaussian beam with FWHM given in kpc,
    which is equal to the amount of pixels if 1 pixel is 1 kpc
    """
    print ("Convolving with a beam FWHM of %i kpc"%FWHM)

    # FWHM to standard deviation divided by pixel size 
    std = FWHM/(2*np.sqrt(2*np.log(2))) / pixsize

    print ("Which is a standard deviation of %.1f pixels"%std)

    if std < 2:
        print ("Since the beam resolution (FWHM %.1f kpc or std %.1f kpc) is so close to the simulated resolution (%.1f kpc), NOT smoothing"%(FWHM,std*pixsize, pixsize))
        return images 

    beam = Gaussian2DKernel(std)
    convolved = []
    for image in images:
        convolved.append(convolve(image,beam,boundary='extend',normalize_kernel=True))
    return convolved